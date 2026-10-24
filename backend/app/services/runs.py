import hashlib
import json
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway.router import ModelRouter
from app.ai.prompts.research_assistant import PROMPT_VERSION, SYSTEM_PROMPT
from app.ai.registry import ModelProfile
from app.ai.runner import RunManager
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.logging import trace_id_var
from app.models import Conversation, Message, Run
from app.models.runs import ACTIVE_RUN_STATUSES
from app.schemas.runs import RunCreate, RunRead


def config_hash(profile: ModelProfile) -> str:
    """Fingerprint of everything that shapes the output besides the conversation itself."""
    config = {
        "profile": profile.model_dump(exclude={"label", "notes"}),
        "prompt_version": PROMPT_VERSION,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
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

        run = Run(
            conversation_id=conversation_id,
            kind="chat",
            status="queued",
            model_profile=profile.id,
            provider=profile.provider,
            model_id=profile.model,
            prompt_version=PROMPT_VERSION,
            config_hash=config_hash(profile),
            context_until_sequence=last.sequence,
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
