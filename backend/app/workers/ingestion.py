"""Ingestion worker: a separate process that drains the `ingestion_jobs` table.

    python -m app.workers.ingestion                # run until SIGTERM/SIGINT
    python -m app.workers.ingestion --healthcheck  # exit 0 if the worker loop is alive (Docker healthcheck)

Jobs are claimed with `FOR UPDATE SKIP LOCKED`, so several workers can run safely. A claim takes a lease
(`locked_until`); if a worker dies, its job becomes claimable again once the lease expires. Transient provider
failures are retried with a growing delay up to `max_attempts`; permanent ones fail immediately.
"""

import argparse
import asyncio
import logging
import signal
import sys
import time
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timedelta

import httpx
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import __version__
from app.ai.gateway.router import ModelRouter
from app.ai.registry import load_registry
from app.core.config import Settings, get_settings
from app.core.errors import AppError, UpstreamError
from app.core.logging import configure_logging, trace_id_var
from app.core.tracing import configure_tracing, span
from app.db.session import create_engine
from app.ingestion.pipeline import IngestionPipeline
from app.integrations.europepmc.client import EuropePMCClient
from app.integrations.pubmed.client import PubMedClient
from app.models import IngestionJob
from app.storage.object_store import FilesystemObjectStore

logger = logging.getLogger(__name__)


async def _db_now(session: AsyncSession) -> datetime:
    """The database clock: job times are compared with `now()` in SQL, so they must come from the same clock."""
    now: datetime = (await session.execute(select(func.now()))).scalar_one()
    return now


class IngestionWorker:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], pipeline: IngestionPipeline, settings: Settings
    ) -> None:
        self._session_factory = session_factory
        self._pipeline = pipeline
        self._settings = settings

    async def claim(self) -> uuid.UUID | None:
        # Scheduling uses the database clock only: `run_after` defaults to the DB's now(), and the worker's host
        # clock may differ from it (a worker running ahead or behind would skip or prematurely take jobs).
        now = func.now()
        async with self._session_factory() as session:
            candidate = (
                select(IngestionJob.id)
                .where(
                    or_(
                        (IngestionJob.status == "queued") & (IngestionJob.run_after <= now),
                        # Lease expired: the worker that held it is gone.
                        (IngestionJob.status == "running") & (IngestionJob.locked_until < now),
                    )
                )
                .order_by(IngestionJob.run_after, IngestionJob.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
                .scalar_subquery()
            )
            job_id = await session.scalar(
                update(IngestionJob)
                .where(IngestionJob.id == candidate)
                .values(
                    status="running",
                    attempts=IngestionJob.attempts + 1,
                    locked_until=now + timedelta(seconds=self._settings.ingestion_lease_s),
                    started_at=now,
                )
                .returning(IngestionJob.id)
            )
            await session.commit()
            return job_id

    async def run_once(self) -> bool:
        """Process one due job. Returns False when the queue had nothing to do."""
        job_id = await self.claim()
        if job_id is None:
            return False
        await self._process(job_id)
        return True

    async def _process(self, job_id: uuid.UUID) -> None:
        async with self._session_factory() as session:
            job = await session.get(IngestionJob, job_id)
            if job is None:
                return
            trace_id_var.set(job.trace_id)
            if job.attempts > job.max_attempts:
                await self._finish(session, job, "failed", "worker_lost", "The worker stopped repeatedly mid-job")
                return
            try:
                with span(
                    "ingestion.job",
                    job_id=str(job.id),
                    source=f"{job.source_type}:{job.external_id}",
                    attempt=job.attempts,
                ):
                    await self._pipeline.run(session, job)
            except UpstreamError as exc:
                await session.rollback()
                await self._retry_or_fail(session, job_id, exc.code, exc.message)
                return
            except AppError as exc:
                await session.rollback()
                await self._finish(session, await self._reload(session, job_id), "failed", exc.code, exc.message)
                return
            except Exception:
                logger.exception("ingestion job crashed", extra={"job_id": str(job_id)})
                await session.rollback()
                await self._finish(
                    session, await self._reload(session, job_id), "failed", "internal_error", "Internal ingestion error"
                )
                return
            await self._finish(session, job, "succeeded", None, None)

    async def _reload(self, session: AsyncSession, job_id: uuid.UUID) -> IngestionJob:
        job = await session.get(IngestionJob, job_id, populate_existing=True)
        assert job is not None
        return job

    async def _retry_or_fail(self, session: AsyncSession, job_id: uuid.UUID, code: str, message: str) -> None:
        job = await self._reload(session, job_id)
        if job.attempts < job.max_attempts:
            job.status = "queued"
            delay = timedelta(seconds=self._settings.ingestion_retry_delay_s * job.attempts)
            job.run_after = await _db_now(session) + delay
            job.locked_until = None
            job.error_code, job.error_message = code, f"Attempt {job.attempts} failed: {message}; will retry"
            await session.commit()
            logger.warning("ingestion job will retry", extra={"job_id": str(job_id), "error_code": code})
        else:
            await self._finish(session, job, "failed", code, message)

    async def _finish(
        self, session: AsyncSession, job: IngestionJob, status: str, code: str | None, message: str | None
    ) -> None:
        job.status = status
        job.error_code, job.error_message = code, message
        job.locked_until = None
        job.finished_at = await _db_now(session)
        await session.commit()
        logger.info(
            "ingestion job finished",
            extra={"job_id": str(job.id), "status": status, "error_code": code, "attempts": job.attempts},
        )

    async def run_forever(self, stop: asyncio.Event, heartbeat: "Heartbeat") -> None:
        while not stop.is_set():
            heartbeat.beat()
            try:
                worked = await self.run_once()
            except Exception:
                # e.g. database briefly unavailable; keep the loop alive and try again.
                logger.exception("worker loop error")
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(stop.wait(), self._settings.ingestion_poll_interval_s)
                except TimeoutError:
                    pass


class Heartbeat:
    def __init__(self, settings: Settings) -> None:
        self._path = settings.worker_heartbeat_path
        self._max_age = settings.ingestion_poll_interval_s * 3 + 30

    def beat(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch()

    def healthy(self) -> bool:
        try:
            return time.time() - self._path.stat().st_mtime < self._max_age
        except FileNotFoundError:
            return False


def build_worker(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
    model_http: httpx.AsyncClient | None = None,
) -> IngestionWorker:
    models = ModelRouter(
        load_registry(settings.model_registry_path),
        model_http or http,
        ollama_base_url=settings.ollama_base_url,
        allow_external=settings.allow_external_models,
    )
    pipeline = IngestionPipeline(
        PubMedClient.from_settings(http, settings),
        EuropePMCClient.from_settings(http, settings),
        FilesystemObjectStore(settings.object_store_path),
        settings.allowed_fulltext_licenses,
        models,
    )
    return IngestionWorker(session_factory, pipeline, settings)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_tracing("bioresearch-worker", settings.otel_exporter_otlp_endpoint)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    async with AsyncExitStack() as stack:
        engine = create_engine(settings)
        stack.push_async_callback(engine.dispose)
        http = await stack.enter_async_context(
            httpx.AsyncClient(
                timeout=settings.ncbi_timeout_s,
                headers={
                    "User-Agent": f"BioResearchAI/{__version__} ({settings.ncbi_email or 'no contact configured'})"
                },
            )
        )
        model_http = await stack.enter_async_context(
            httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=settings.model_connect_timeout_s, read=settings.model_read_timeout_s, write=30, pool=5
                )
            )
        )
        worker = build_worker(settings, async_sessionmaker(engine, expire_on_commit=False), http, model_http)
        logger.info("ingestion worker started", extra={"version": __version__})
        await worker.run_forever(stop, Heartbeat(settings))
        logger.info("ingestion worker stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--healthcheck", action="store_true", help="exit 0 if the worker loop is alive")
    args = parser.parse_args()
    if args.healthcheck:
        sys.exit(0 if Heartbeat(get_settings()).healthy() else 1)
    asyncio.run(main())
