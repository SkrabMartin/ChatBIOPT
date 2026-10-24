"""Executes chat runs off the request path and fans their events out to any number of SSE subscribers.

Runs are short (one model response), so an in-process asyncio task is the right tool; long scientific jobs will
need a durable queue (M10/M11). State that matters is in the database: if the process dies mid-run, startup
recovery marks the run failed (`interrupted`), and finished runs are replayed to late subscribers from the DB.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.context import BuiltContext, ContextTooLongError, build_context
from app.ai.gateway.base import GatewayError, GenerationRequest, StreamEnd, TextDelta
from app.ai.gateway.router import ModelRouter
from app.ai.grounding import (
    EXTRACTION_VERSION,
    ParsedAnswer,
    insufficient_evidence_message,
    label,
    parse_answer,
    render_evidence,
)
from app.ai.prompts.research_assistant import GROUNDED_SYSTEM_PROMPT
from app.ai.registry import ModelProfile
from app.core.errors import AppError
from app.core.logging import trace_id_var
from app.core.tracing import span
from app.models import Claim, ClaimEvidence, Conversation, Message, ProjectSource, Run, RunEvidence
from app.retrieval.embeddings import EmbeddingSpace, resolve_space
from app.retrieval.rerank import resolve_reranker
from app.retrieval.search import Hit, RetrievalResult, retrieve
from app.schemas.runs import RunRead
from app.schemas.workspace import MessageRead
from app.services.conversations import append_message

logger = logging.getLogger(__name__)

TERMINAL_EVENTS = frozenset({"completed", "failed", "cancelled"})


class RetrievalFailedError(AppError):
    status_code = 502
    code = "tool_error"


def _evidence_json(index: int, hit: Hit) -> dict[str, Any]:
    return {
        "label": label(index),
        "chunk_id": str(hit.chunk_id),
        "document_id": str(hit.document_id),
        "source_title": hit.source_title,
        "external_id": hit.external_id,
        "section": hit.section,
        "section_title": hit.section_title,
    }


@dataclass(frozen=True)
class RunEvent:
    id: int
    type: str
    data: dict[str, Any]


class RunChannel:
    """Append-only event log for one run. Subscribers replay from any point, then follow live."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []
        self.closed = False
        self._changed = asyncio.Condition()

    async def publish(self, type_: str, data: dict[str, Any]) -> None:
        async with self._changed:
            self.events.append(RunEvent(len(self.events) + 1, type_, data))
            self.closed = type_ in TERMINAL_EVENTS
            self._changed.notify_all()

    async def follow(self, after: int, heartbeat_s: float) -> AsyncIterator[RunEvent | None]:
        """Events with id > `after`; yields None as a keep-alive when nothing happened for `heartbeat_s`."""
        position = after
        while True:
            async with self._changed:
                if position >= len(self.events) and not self.closed:
                    try:
                        await asyncio.wait_for(self._changed.wait(), heartbeat_s)
                    except TimeoutError:
                        pass
                pending, closed = self.events[position:], self.closed
            for event in pending:
                position = event.id
                yield event
            if closed and position >= len(self.events):
                return
            if not pending:
                yield None


class RunManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        router: ModelRouter,
        *,
        max_duration_s: float,
        rag_top_k: int = 8,
        retain_channel_s: float = 120,
    ) -> None:
        self._session_factory = session_factory
        self._router = router
        self._max_duration_s = max_duration_s
        self._rag_top_k = rag_top_k
        self._retain_channel_s = retain_channel_s
        self._channels: dict[uuid.UUID, RunChannel] = {}
        self._tasks: dict[uuid.UUID, asyncio.Task[None]] = {}

    def channel(self, run_id: uuid.UUID) -> RunChannel | None:
        return self._channels.get(run_id)

    def start(self, run_id: uuid.UUID) -> None:
        self._channels[run_id] = RunChannel()
        task = asyncio.create_task(self._execute(run_id, trace_id_var.get()), name=f"run-{run_id}")
        self._tasks[run_id] = task

    def cancel(self, run_id: uuid.UUID) -> bool:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _execute(self, run_id: uuid.UUID, trace_id: str | None) -> None:
        trace_id_var.set(trace_id)
        channel = self._channels[run_id]
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._max_duration_s):
                with span("chat.run", run_id=str(run_id)):
                    await self._generate(run_id, channel, started)
        except GatewayError as exc:
            await self._fail(run_id, channel, started, exc.code, exc.message)
        except AppError as exc:
            await self._fail(run_id, channel, started, exc.code, exc.message)
        except TimeoutError:
            await self._fail(run_id, channel, started, "run_timeout", "The model did not finish within the time limit")
        except asyncio.CancelledError:
            await self._finish(run_id, started, status="cancelled")
            await channel.publish("cancelled", {"run": await self._run_json(run_id)})
        except Exception:
            logger.exception("run crashed", extra={"run_id": str(run_id)})
            await self._fail(run_id, channel, started, "internal_error", "Internal error while generating")
        finally:
            self._tasks.pop(run_id, None)
            # Keep the event log briefly so a client that reconnects right after the end still gets the deltas.
            asyncio.get_running_loop().call_later(self._retain_channel_s, self._channels.pop, run_id, None)

    async def _generate(self, run_id: uuid.UUID, channel: RunChannel, started: float) -> None:
        async with self._session_factory() as session:
            run = await session.get(Run, run_id)
            if run is None:  # conversation deleted before the run began
                await channel.publish("failed", {"error_code": "not_found", "message": "Run no longer exists"})
                return
            profile = self._router.profile(run.model_profile)
            gateway = self._router.gateway(profile)
            history = list(
                (
                    await session.scalars(
                        select(Message)
                        .where(Message.conversation_id == run.conversation_id)
                        .where(Message.sequence <= run.context_until_sequence)
                        .order_by(Message.sequence)
                    )
                ).all()
            )
            conversation_id = run.conversation_id
            grounded = run.grounding == "project"
            evidence: list[Hit] = []
            timings: dict[str, Any] = {}
            if grounded:
                project_id = await session.scalar(
                    select(Conversation.project_id).where(Conversation.id == conversation_id)
                )
                assert project_id is not None
                await channel.publish("status", {"status": "retrieving"})
                retrieval, min_similarity = await self._retrieve(
                    session, project_id, history[-1].content, run.rerank_profile, timings
                )
                evidence = [h for h in retrieval.hits if h.is_evidence(min_similarity)][: self._rag_top_k]
                run.retrieval_version = retrieval.version
                run.embedding_space = retrieval.embedding_space
                await channel.publish(
                    "evidence",
                    {"items": [_evidence_json(i, h) for i, h in enumerate(evidence)], "warnings": retrieval.warnings},
                )
                if not evidence:
                    # Abstain without calling the model: there is nothing to ground an answer in.
                    sources = await session.scalar(
                        select(func.count()).select_from(ProjectSource).where(ProjectSource.project_id == project_id)
                    )
                    await session.commit()
                    await self._complete(
                        run_id,
                        conversation_id,
                        channel,
                        started,
                        insufficient_evidence_message(sources or 0, retrieval.warnings),
                        answer_class="insufficient_evidence",
                        citation_stats={"evidence": 0, "claims": 0},
                        timings=timings,
                    )
                    return

            availability = await gateway.availability()
            if not availability.available:
                raise GatewayError("model_unavailable", availability.reason or "Model is not available")
            if grounded:
                context, evidence = self._grounded_context(history, evidence, profile)
            else:
                context = build_context(
                    history, context_window=profile.context_window, max_output_tokens=profile.max_output_tokens
                )
            run.status = "running"
            run.started_at = datetime.now(UTC)
            run.model_revision = availability.revision
            run.context_message_count = context.history_message_count
            await session.commit()
        await channel.publish("status", {"status": "running", "model_revision": availability.revision})

        parts: list[str] = []
        first_token_ms: int | None = None
        end: StreamEnd | None = None
        request = GenerationRequest(
            messages=context.messages,
            temperature=profile.temperature,
            max_output_tokens=profile.max_output_tokens,
            context_window=profile.context_window,
        )
        generation_started = time.perf_counter()
        with span("chat.generation", model=profile.model, grounded=grounded) as generation_span:
            async for event in gateway.stream(request):
                if isinstance(event, TextDelta):
                    if first_token_ms is None:
                        first_token_ms = _ms_since(started)
                    parts.append(event.text)
                    await channel.publish("delta", {"text": event.text})
                else:
                    end = event
            if end is not None:
                generation_span.set_attribute("tokens.input", end.input_tokens or 0)
                generation_span.set_attribute("tokens.output", end.output_tokens or 0)
        timings["generation_ms"] = _ms_since(generation_started)
        content = "".join(parts)
        if end is None or not content.strip():
            # Never store or show an empty/partial answer as if it were a response.
            raise GatewayError("model_error", "The model returned an empty response")

        usage: dict[str, Any] = {
            "input_tokens": end.input_tokens,
            "output_tokens": end.output_tokens,
            "estimated_cost_usd": profile.estimate_cost_usd(end.input_tokens, end.output_tokens),
            "time_to_first_token_ms": first_token_ms,
            "finish_reason": end.finish_reason,
            "timings": timings,
        }
        if not grounded:
            await self._complete(run_id, conversation_id, channel, started, content, **usage)
        else:
            labels = {label(i): hit for i, hit in enumerate(evidence)}
            parsed = parse_answer(
                content,
                set(labels),
                allowed_pmids={hit.external_id for hit in evidence},
                allowed_urls={hit.source_url for hit in evidence},
            )
            await self._complete(
                run_id,
                conversation_id,
                channel,
                started,
                parsed.content or "The model reported insufficient evidence without further explanation.",
                evidence=labels,
                parsed=parsed,
                answer_class=parsed.answer_class,
                citation_stats=parsed.stats,
                **usage,
            )
        logger.info(
            "run succeeded",
            extra={
                "run_id": str(run_id),
                "model_profile": profile.id,
                "grounded": grounded,
                "input_tokens": end.input_tokens,
                "output_tokens": end.output_tokens,
                "ttft_ms": first_token_ms,
                "latency_ms": _ms_since(started),
            },
        )

    async def _retrieve(
        self,
        session: AsyncSession,
        project_id: uuid.UUID,
        question: str,
        rerank_profile: str | None,
        timings: dict[str, Any],
    ) -> tuple[RetrievalResult, float | None]:
        space: EmbeddingSpace | None = None
        space_error: str | None = None
        try:
            space = await resolve_space(self._router)
        except (GatewayError, AppError) as exc:
            space_error = str(exc)
        reranker = resolve_reranker(self._router, rerank_profile) if rerank_profile else None
        try:
            with span("chat.retrieval", rerank_profile=rerank_profile) as retrieval_span:
                result = await retrieve(
                    session,
                    project_id,
                    question,
                    mode="hybrid",
                    top_k=self._rag_top_k * 3,
                    space=space,
                    space_error=space_error,
                    reranker=reranker,
                )
                retrieval_span.set_attribute("hits", len(result.hits))
        except (SQLAlchemyError, GatewayError) as exc:
            # A retrieval failure is a tool error, never "no evidence".
            raise RetrievalFailedError(f"Evidence retrieval failed: {exc}") from exc
        timings["retrieval_ms"] = result.latency_ms
        if result.rerank_ms is not None and reranker is not None:
            timings.update(
                rerank_ms=result.rerank_ms,
                rerank_graded=reranker.last_graded,
                rerank_input_tokens=reranker.last_input_tokens,
                rerank_output_tokens=reranker.last_output_tokens,
            )
        return result, space.profile.min_similarity if space else None

    def _grounded_context(
        self, history: list[Message], evidence: list[Hit], profile: ModelProfile
    ) -> tuple[BuiltContext, list[Hit]]:
        """Fit as much top-ranked evidence as the window allows; history fills what is left."""
        question = history[-1].content
        while evidence:
            latest = f"{question}\n\n{render_evidence(evidence)}"
            try:
                context = build_context(
                    history,
                    context_window=profile.context_window,
                    max_output_tokens=profile.max_output_tokens,
                    system_prompt=GROUNDED_SYSTEM_PROMPT,
                    latest_user_content=latest,
                )
                return context, evidence
            except ContextTooLongError:
                evidence = evidence[:-1]
        raise ContextTooLongError("The question and its evidence do not fit this model's context window")

    async def _complete(
        self,
        run_id: uuid.UUID,
        conversation_id: uuid.UUID,
        channel: RunChannel,
        started: float,
        content: str,
        *,
        evidence: dict[str, Hit] | None = None,
        parsed: ParsedAnswer | None = None,
        **run_values: Any,
    ) -> None:
        async with self._session_factory() as session:
            message = await append_message(session, conversation_id, role="assistant", content=content, run_id=run_id)
            if evidence:
                cited = parsed.cited_labels if parsed else set()
                session.add_all(
                    RunEvidence(
                        run_id=run_id,
                        label=lab,
                        rank=index + 1,
                        chunk_id=hit.chunk_id,
                        keyword_rank=hit.keyword_rank,
                        semantic_rank=hit.semantic_rank,
                        similarity=hit.similarity,
                        fused_score=hit.fused_score,
                        rerank_score=hit.rerank_score,
                        cited=lab in cited,
                    )
                    for index, (lab, hit) in enumerate(evidence.items())
                )
            # An abstention is not a set of scientific claims; keep it off the evidence board.
            if parsed and evidence and parsed.answer_class != "insufficient_evidence":
                project_id = await session.scalar(
                    select(Conversation.project_id).where(Conversation.id == conversation_id)
                )
                assert project_id is not None
                for draft in parsed.claims:
                    claim = Claim(
                        project_id=project_id,
                        run_id=run_id,
                        message_id=message.id,
                        ordinal=draft.ordinal,
                        claim_text=draft.text,
                        support_state="cited" if draft.labels else "uncited",
                        extraction_version=EXTRACTION_VERSION,
                    )
                    session.add(claim)
                    await session.flush()
                    session.add_all(
                        ClaimEvidence(claim_id=claim.id, chunk_id=evidence[lab].chunk_id, label=lab)
                        for lab in draft.labels
                    )
            await session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(status="succeeded", latency_ms=_ms_since(started), finished_at=datetime.now(UTC), **run_values)
            )
            await session.commit()
            message_json = MessageRead.model_validate(message).model_dump(mode="json")
        await channel.publish("completed", {"run": await self._run_json(run_id), "message": message_json})

    async def _fail(self, run_id: uuid.UUID, channel: RunChannel, started: float, code: str, message: str) -> None:
        logger.warning("run failed", extra={"run_id": str(run_id), "error_code": code})
        # A broken tool is reported as such; it must never look like "no evidence".
        answer_class = "tool_error" if code == "tool_error" else None
        await self._finish(
            run_id, started, status="failed", error_code=code, error_message=message, answer_class=answer_class
        )
        await channel.publish("failed", {"error_code": code, "message": message, "run": await self._run_json(run_id)})

    async def _finish(self, run_id: uuid.UUID, started: float, **values: Any) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(**values, latency_ms=_ms_since(started), finished_at=datetime.now(UTC))
            )
            await session.commit()

    async def _run_json(self, run_id: uuid.UUID) -> dict[str, Any] | None:
        async with self._session_factory() as session:
            run = await session.get(Run, run_id)
            return RunRead.model_validate(run).model_dump(mode="json") if run else None


def _ms_since(start: float) -> int:
    return round((time.perf_counter() - start) * 1000)
