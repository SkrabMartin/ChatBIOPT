import hashlib
import json
import uuid

from sqlalchemy import exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway.router import ModelRouter, UnknownModelProfileError
from app.ai.prompts.research_assistant import (
    GROUNDED_PROMPT_VERSION,
    GROUNDED_SYSTEM_PROMPT,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
)
from app.ai.registry import EmbeddingProfile, ModelProfile, RerankProfile
from app.ai.runner import RunManager
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.logging import trace_id_var
from app.ingestion.chunking import CHUNKER_VERSION
from app.models import Chunk, Conversation, Document, Message, ProjectSource, Run, RunEvidence, Source
from app.models.runs import ACTIVE_RUN_STATUSES
from app.retrieval.rerank import RERANK_VERSION
from app.retrieval.search import RETRIEVAL_VERSION
from app.schemas.runs import RunCreate, RunEvidenceRead, RunRead


def config_hash(
    profile: ModelProfile,
    grounding: str,
    embedding_profile: EmbeddingProfile | None = None,
    rerank_profile: RerankProfile | None = None,
) -> str:
    """Fingerprint of everything that shapes the output besides the conversation itself."""
    grounded = grounding == "project"
    prompt = GROUNDED_SYSTEM_PROMPT if grounded else SYSTEM_PROMPT
    config = {
        "profile": profile.model_dump(exclude={"label", "notes"}),
        "prompt_version": GROUNDED_PROMPT_VERSION if grounded else PROMPT_VERSION,
        "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "grounding": grounding,
    }
    if grounded:
        config["retrieval"] = {
            "version": RETRIEVAL_VERSION,
            "chunker": CHUNKER_VERSION,
            "embedding_profile": embedding_profile.model_dump(exclude={"label", "notes"})
            if embedding_profile
            else None,
            "rerank_profile": rerank_profile.model_dump(exclude={"label", "notes"}) if rerank_profile else None,
            "rerank_version": RERANK_VERSION if rerank_profile else None,
        }
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


class RunService:
    def __init__(self, session: AsyncSession, router: ModelRouter, manager: RunManager) -> None:
        self.session = session
        self.router = router
        self.manager = manager

    async def create_chat_run(self, conversation_id: uuid.UUID, data: RunCreate) -> RunRead:
        if await self.session.get(Conversation, conversation_id) is None:
            raise NotFoundError("Conversation not found")
        last = await self.session.scalar(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.sequence.desc()).limit(1)
        )
        if last is None or last.role != "user":
            raise ConflictError("There is no new user message to answer")
        profile = self.router.profile(data.model_profile)
        if (reason := self.router.policy_block_reason(profile)) is not None:
            raise ForbiddenError(reason)
        grounding = await self._resolve_grounding(conversation_id, data.grounding)
        embedding_profile = None
        rerank_profile = None
        if grounding == "project":
            if self.router.registry.default_embedding_profile:
                embedding_profile = self.router.embedding_profile()
            rerank_profile = self._rerank_profile(data)

        run = Run(
            conversation_id=conversation_id,
            kind="chat",
            status="queued",
            model_profile=profile.id,
            provider=profile.provider,
            model_id=profile.model,
            prompt_version=GROUNDED_PROMPT_VERSION if grounding == "project" else PROMPT_VERSION,
            config_hash=config_hash(profile, grounding, embedding_profile, rerank_profile),
            rerank_profile=rerank_profile.id if rerank_profile else None,
            context_until_sequence=last.sequence,
            grounding=grounding,
            trace_id=trace_id_var.get(),
        )
        self.session.add(run)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("A response is already being generated for this conversation") from exc
        self.manager.start(run.id)
        return RunRead.model_validate(run)

    def _rerank_profile(self, data: RunCreate) -> RerankProfile | None:
        registry = self.router.registry
        if data.rerank is False:
            return None
        key = data.rerank_profile or registry.default_rerank_profile
        if key is None:
            if data.rerank:
                raise UnknownModelProfileError("Reranking requested but no rerank profile is configured")
            return None
        rerank = registry.rerank_profiles.get(key)
        if rerank is None:
            raise UnknownModelProfileError(f"Unknown rerank profile {key!r}")
        if (reason := self.router.policy_block_reason(self.router.profile(rerank.generation_profile))) is not None:
            raise ForbiddenError(f"Reranker model blocked: {reason}")
        return rerank

    async def _resolve_grounding(self, conversation_id: uuid.UUID, requested: str) -> str:
        """`auto` grounds in the project's library when it has any chunks; otherwise answers ungrounded."""
        if requested != "auto":
            return requested
        has_chunks = await self.session.scalar(
            select(
                exists()
                .where(Conversation.id == conversation_id)
                .where(ProjectSource.project_id == Conversation.project_id)
                .where(Document.source_id == ProjectSource.source_id)
                .where(Chunk.document_id == Document.id)
            )
        )
        return "project" if has_chunks else "none"

    async def evidence(self, run_id: uuid.UUID) -> list[RunEvidenceRead]:
        await self.get(run_id)
        rows = (
            await self.session.execute(
                select(RunEvidence, Chunk, Document, Source)
                .join(Chunk, Chunk.id == RunEvidence.chunk_id)
                .join(Document, Document.id == Chunk.document_id)
                .join(Source, Source.id == Document.source_id)
                .where(RunEvidence.run_id == run_id)
                .order_by(RunEvidence.rank)
            )
        ).all()
        return [
            RunEvidenceRead(
                label=ev.label,
                rank=ev.rank,
                cited=ev.cited,
                keyword_rank=ev.keyword_rank,
                semantic_rank=ev.semantic_rank,
                similarity=ev.similarity,
                fused_score=ev.fused_score,
                rerank_score=ev.rerank_score,
                chunk_id=chunk.id,
                text=chunk.text,
                section=chunk.section,
                section_title=chunk.section_title,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                document_id=document.id,
                document_kind=document.kind,
                document_version=document.version,
                source_id=source.id,
                external_id=source.external_id,
                source_title=source.title,
                source_url=source.url,
            )
            for ev, chunk, document, source in rows
        ]

    async def get(self, run_id: uuid.UUID) -> RunRead:
        run = await self.session.get(Run, run_id)
        if run is None:
            raise NotFoundError("Run not found")
        return RunRead.model_validate(run)

    async def list_for_conversation(self, conversation_id: uuid.UUID, *, limit: int) -> list[RunRead]:
        if await self.session.get(Conversation, conversation_id) is None:
            raise NotFoundError("Conversation not found")
        runs = await self.session.scalars(
            select(Run)
            .where(Run.conversation_id == conversation_id)
            .order_by(Run.created_at.desc(), Run.id)
            .limit(limit)
        )
        return [RunRead.model_validate(r) for r in runs]

    async def cancel(self, run_id: uuid.UUID) -> RunRead:
        run = await self.get(run_id)
        if run.status not in ACTIVE_RUN_STATUSES or not self.manager.cancel(run_id):
            raise ConflictError("Run is not active")
        return run

    async def final_message(self, run_id: uuid.UUID) -> Message | None:
        message: Message | None = await self.session.scalar(select(Message).where(Message.run_id == run_id))
        return message


async def recover_interrupted_runs(session: AsyncSession) -> int:
    """Runs left queued/running by a previous process can never finish; close them honestly."""
    result = await session.execute(
        update(Run)
        .where(Run.status.in_(ACTIVE_RUN_STATUSES))
        .values(
            status="failed",
            error_code="interrupted",
            error_message="The server restarted before this response finished",
            finished_at=func.now(),
        )
        .returning(Run.id)
    )
    count = len(result.all())
    await session.commit()
    return count
