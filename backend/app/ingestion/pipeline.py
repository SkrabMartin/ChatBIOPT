"""Evidence ingestion for one PubMed record:

metadata (PubMed) -> abstract document -> open-access check (Europe PMC) -> full text (license-gated) -> verify.

Every step is idempotent and logged on the job; progress is committed step by step, so a retry after a
transient failure resumes cheaply and never duplicates documents or chunks.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.ai.gateway.base import GatewayError
from app.ai.gateway.router import ModelRouter
from app.core.errors import AppError, NotFoundError
from app.core.tracing import span
from app.ingestion.chunking import CHUNKER_VERSION, Section
from app.ingestion.documents import DocumentInput, store_document
from app.ingestion.licensing import decide_full_text, normalize_license
from app.integrations.europepmc.client import EuropePMCClient
from app.integrations.europepmc.jats import PARSER_VERSION as JATS_PARSER_VERSION
from app.integrations.europepmc.jats import parse_jats
from app.integrations.pubmed.client import PubMedClient
from app.models import Chunk, Document, IngestionJob, ProjectSource, Source, SourceIdentifier
from app.retrieval.embeddings import embed_missing_chunks, resolve_space
from app.schemas.literature import LiteratureRecord
from app.services.literature import upsert_source
from app.storage.object_store import ObjectStore, ObjectStoreError, sha256_hex

ABSTRACT_PARSER_VERSION = "pubmed-abstract-v1"


class IntegrityCheckError(AppError):
    status_code = 500
    code = "integrity_error"


@dataclass
class StepLog:
    """Ordered step records persisted on the job after every step."""

    steps: list[dict[str, Any]] = field(default_factory=list)

    def add(self, step: str, status: str, detail: str, **data: Any) -> None:
        self.steps.append(
            {"step": step, "status": status, "detail": detail, "at": datetime.now(UTC).isoformat(), **data}
        )


def abstract_sections(record: LiteratureRecord) -> list[Section]:
    sections = [Section("title", None, [record.title])]
    for part in record.abstract_sections:
        sections.append(Section("abstract", f"Abstract > {part.label}" if part.label else "Abstract", [part.text]))
    return sections


class IngestionPipeline:
    def __init__(
        self,
        pubmed: PubMedClient,
        europepmc: EuropePMCClient,
        store: ObjectStore,
        allowed_licenses: frozenset[str],
        models: ModelRouter | None = None,
    ) -> None:
        self.pubmed = pubmed
        self.europepmc = europepmc
        self.store = store
        self.allowed_licenses = allowed_licenses
        self.models = models

    async def run(self, session: AsyncSession, job: IngestionJob) -> StepLog:
        log = StepLog()

        async def checkpoint() -> None:
            job.steps = list(log.steps)
            await session.commit()

        # 1. metadata
        fetched = await self.pubmed.fetch([job.external_id])
        if not fetched:
            raise NotFoundError(f"PubMed has no record with PMID {job.external_id}")
        record = fetched[0].record
        source_id = await upsert_source(session, fetched[0])
        await session.execute(
            insert(ProjectSource).values(project_id=job.project_id, source_id=source_id).on_conflict_do_nothing()
        )
        job.source_id = source_id
        source = await session.get(Source, source_id, populate_existing=True)
        assert source is not None
        log.add(
            "metadata",
            "done",
            f"PubMed record {record.pmid} ({record.normalizer_version})",
            raw_payload_sha256=record.raw_payload_sha256,
        )
        await checkpoint()

        # 2. abstract document
        if record.abstract_sections:
            stored = await store_document(
                session,
                self.store,
                source,
                DocumentInput(
                    kind="abstract",
                    sections=abstract_sections(record),
                    raw_payload=fetched[0].raw_payload.encode(),
                    raw_extension="xml",
                    origin="pubmed",
                    origin_url=record.source_url,
                    access="metadata",
                    license=None,
                    license_url=None,
                    parser_version=ABSTRACT_PARSER_VERSION,
                    retrieved_at=record.retrieved_at,
                ),
            )
            log.add(
                "abstract",
                "done" if stored.created else "unchanged",
                _version_detail(stored.document, stored.created),
                document_id=str(stored.document.id),
                version=stored.document.version,
                chunks_created=stored.chunks_created,
            )
        else:
            log.add("abstract", "skipped", "PubMed record has no abstract")
        await checkpoint()

        # 3. open-access status and license
        info = await self.europepmc.open_access_info(record.pmid)
        if info.pmcid:
            await session.execute(
                insert(SourceIdentifier)
                .values(source_id=source_id, scheme="pmcid", value=info.pmcid)
                .on_conflict_do_nothing()
            )
        decision = decide_full_text(
            is_open_access=info.is_open_access, license_value=info.license, allowed=self.allowed_licenses
        )
        allowed = decision.allowed and info.pmcid is not None
        log.add(
            "open_access",
            "done",
            decision.reason if info.pmcid or not decision.allowed else "no PMCID",
            pmcid=info.pmcid,
            is_open_access=info.is_open_access,
            provider_license=info.license,
            license=decision.license,
            fulltext_allowed=allowed,
        )
        await checkpoint()

        # 4. full text (license-gated)
        if allowed and info.pmcid:
            with span("ingestion.fulltext", pmcid=info.pmcid):
                await self._full_text(session, source, info.pmcid, decision.license, log)
        else:
            log.add(
                "fulltext",
                "skipped",
                f"Full text not stored: {decision.reason if not decision.allowed else 'no PMCID'}",
            )
        await checkpoint()

        # 5. embed chunks of the latest versions into the current embedding space
        with span("ingestion.embed"):
            await self._embed(session, source_id, log)
        await checkpoint()

        # 6. verify what is stored
        with span("ingestion.verify"):
            await self._verify(session, source_id, log)
        await checkpoint()
        return log

    async def _embed(self, session: AsyncSession, source_id: uuid.UUID, log: StepLog) -> None:
        if self.models is None:
            log.add("embed", "skipped", "No embedding model configured")
            return
        try:
            space = await resolve_space(self.models)
        except (GatewayError, AppError) as exc:
            # Documents are still usable for keyword retrieval; a later refresh embeds what is missing.
            log.add("embed", "skipped", f"Embeddings not created: {exc}")
            return
        other = aliased(Document)
        newest_version = (
            select(func.max(other.version))
            .where(other.source_id == Document.source_id, other.kind == Document.kind)
            .scalar_subquery()
        )
        latest = select(Document.id).where(Document.source_id == source_id, Document.version == newest_version)
        chunk_ids = list(
            await session.scalars(
                select(Chunk.id).where(Chunk.document_id.in_(latest), Chunk.chunker_version == CHUNKER_VERSION)
            )
        )
        try:
            created = await embed_missing_chunks(session, space, chunk_ids)
        except GatewayError as exc:
            await session.rollback()
            log.add("embed", "skipped", f"Embeddings not created: {exc.message}")
            return
        log.add(
            "embed",
            "done" if created else "unchanged",
            f"{created} chunk(s) embedded" if created else "All chunks already embedded",
            embedding_space=space.name,
            chunks=len(chunk_ids),
        )

    async def _full_text(
        self, session: AsyncSession, source: Source, pmcid: str, license_id: str | None, log: StepLog
    ) -> None:
        xml = await self.europepmc.full_text_xml(pmcid)
        article = parse_jats(xml)
        jats_license = normalize_license(article.license_url) or normalize_license(article.license_type)
        if jats_license is not None and jats_license not in self.allowed_licenses:
            log.add("fulltext", "skipped", f"Article XML declares {jats_license}, which is not allowed")
            return
        if not any(s.kind not in ("title", "abstract") for s in article.sections):
            log.add("fulltext", "skipped", "Full-text XML has no body text")
            return
        stored = await store_document(
            session,
            self.store,
            source,
            DocumentInput(
                kind="fulltext",
                sections=article.sections,
                raw_payload=xml,
                raw_extension="xml",
                origin="europepmc",
                origin_url=self.europepmc.full_text_url(pmcid),
                access="open_access",
                license=license_id,
                license_url=article.license_url,
                parser_version=JATS_PARSER_VERSION,
                retrieved_at=datetime.now(UTC),
            ),
        )
        log.add(
            "fulltext",
            "done" if stored.created else "unchanged",
            _version_detail(stored.document, stored.created),
            document_id=str(stored.document.id),
            version=stored.document.version,
            chunks_created=stored.chunks_created,
            license=license_id,
        )

    async def _verify(self, session: AsyncSession, source_id: uuid.UUID, log: StepLog) -> None:
        documents = (await session.scalars(select(Document).where(Document.source_id == source_id))).all()
        checked_chunks = 0
        for document in documents:
            try:
                text_bytes = self.store.get(document.text_storage_key)
                raw = self.store.get(document.raw_storage_key)
            except ObjectStoreError as exc:
                raise IntegrityCheckError(f"Stored blob missing for document {document.id}") from exc
            if sha256_hex(text_bytes) != document.text_sha256 or sha256_hex(raw) != document.raw_sha256:
                raise IntegrityCheckError(f"Checksum mismatch for document {document.id}")
            text = text_bytes.decode()
            chunks = (
                await session.scalars(
                    select(Chunk).where(Chunk.document_id == document.id, Chunk.chunker_version == CHUNKER_VERSION)
                )
            ).all()
            for chunk in chunks:
                if text[chunk.start_offset : chunk.end_offset] != chunk.text:
                    raise IntegrityCheckError(f"Chunk {chunk.id} does not match its document offsets")
            checked_chunks += len(chunks)
        log.add("verify", "done", f"{len(documents)} document version(s), {checked_chunks} chunks verified")


def _version_detail(document: Document, created: bool) -> str:
    label = "full text" if document.kind == "fulltext" else "abstract"
    if created:
        return f"Stored {label} v{document.version}"
    return f"{label.capitalize()} unchanged (v{document.version})"
