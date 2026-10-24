import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway.router import ModelRouter
from app.ai.runner import RunManager
from app.db.session import get_session
from app.schemas.runs import ModelList, ModelProfileRead, RerankProfileRead, RunCreate, RunEvidenceRead, RunRead
from app.schemas.workspace import MessageRead
from app.services.runs import RunService

router = APIRouter()

SSE_HEARTBEAT_S = 15.0


def get_model_router(request: Request) -> ModelRouter:
    model_router: ModelRouter = request.app.state.model_router
    return model_router


def get_run_manager(request: Request) -> RunManager:
    manager: RunManager = request.app.state.run_manager
    return manager


def run_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    model_router: Annotated[ModelRouter, Depends(get_model_router)],
    manager: Annotated[RunManager, Depends(get_run_manager)],
) -> RunService:
    return RunService(session, model_router, manager)


Runs = Annotated[RunService, Depends(run_service)]


@router.get("/models", response_model=ModelList, tags=["models"])
async def list_models(model_router: Annotated[ModelRouter, Depends(get_model_router)]) -> ModelList:
    """Registry profiles with their policy gate and live availability (pulled / served)."""
    registry = model_router.registry

    async def describe(profile_id: str) -> ModelProfileRead:
        profile = registry.profiles[profile_id]
        blocked = model_router.policy_block_reason(profile)
        availability = None if blocked else await model_router.gateway(profile).availability()
        return ModelProfileRead(
            **profile.model_dump(exclude={"id", "base_url", "api_key_env", "temperature"}),
            id=profile.id,
            is_default=profile.id == registry.default_profile,
            blocked_reason=blocked,
            available=bool(availability and availability.available),
            unavailable_reason=blocked or (availability.reason if availability else None),
            revision=availability.revision if availability else None,
        )

    items = await asyncio.gather(*(describe(pid) for pid in registry.profiles))
    return ModelList(
        default_profile=registry.default_profile,
        allow_external_models=model_router.allow_external,
        items=list(items),
        rerank_profiles=[
            RerankProfileRead(
                id=r.id,
                label=r.label,
                generation_profile=r.generation_profile,
                is_default=r.id == registry.default_rerank_profile,
            )
            for r in registry.rerank_profiles.values()
        ],
    )


@router.post(
    "/chat/{conversation_id}/runs",
    response_model=RunRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
)
async def create_chat_run(conversation_id: uuid.UUID, data: RunCreate, runs: Runs) -> RunRead:
    """Start generating an answer to the conversation's latest user message. Follow it via the events stream."""
    return await runs.create_chat_run(conversation_id, data)


@router.get("/conversations/{conversation_id}/runs", response_model=list[RunRead], tags=["runs"])
async def list_conversation_runs(
    conversation_id: uuid.UUID, runs: Runs, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[RunRead]:
    return await runs.list_for_conversation(conversation_id, limit=limit)


@router.get("/runs/{run_id}", response_model=RunRead, tags=["runs"])
async def get_run(run_id: uuid.UUID, runs: Runs) -> RunRead:
    return await runs.get(run_id)


@router.post("/runs/{run_id}/cancel", response_model=RunRead, status_code=status.HTTP_202_ACCEPTED, tags=["runs"])
async def cancel_run(run_id: uuid.UUID, runs: Runs) -> RunRead:
    return await runs.cancel(run_id)


@router.get("/runs/{run_id}/evidence", response_model=list[RunEvidenceRead], tags=["runs"])
async def run_evidence(run_id: uuid.UUID, runs: Runs) -> list[RunEvidenceRead]:
    """Passages given to a grounded run as [E#] labels, with ranks and whether the answer cited them."""
    return await runs.evidence(run_id)


def _sse(event_type: str, data: dict[str, Any], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event_type}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


@router.get(
    "/runs/{run_id}/events",
    tags=["runs"],
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Server-sent events"}},
)
async def run_events(
    run_id: uuid.UUID,
    runs: Runs,
    manager: Annotated[RunManager, Depends(get_run_manager)],
    last_event_id: Annotated[int | None, Header(alias="Last-Event-ID", ge=0)] = None,
    after: Annotated[int | None, Query(ge=0, description="Resume after this event id")] = None,
) -> StreamingResponse:
    """SSE: `status`, `delta` {text}, then exactly one of `completed` {run, message}, `failed`
    {error_code, message, run} or `cancelled` {run}. Resumable via Last-Event-ID while the run is live;
    afterwards the outcome is replayed from the database."""
    run = await runs.get(run_id)
    channel = manager.channel(run_id)

    if channel is not None:
        start = last_event_id if last_event_id is not None else (after or 0)

        async def live() -> AsyncIterator[str]:
            async for event in channel.follow(start, SSE_HEARTBEAT_S):
                yield ": keep-alive\n\n" if event is None else _sse(event.type, event.data, event.id)

        body: AsyncIterator[str] = live()
    else:
        run_json = run.model_dump(mode="json")
        if run.status == "succeeded":
            message = await runs.final_message(run_id)
            message_json = MessageRead.model_validate(message).model_dump(mode="json") if message else None
            final = _sse("completed", {"run": run_json, "message": message_json})
        elif run.status == "cancelled":
            final = _sse("cancelled", {"run": run_json})
        else:
            # failed, or queued/running with no live task (only possible before startup recovery ran)
            final = _sse(
                "failed",
                {
                    "error_code": run.error_code or "interrupted",
                    "message": run.error_message or "This response is no longer being generated",
                    "run": run_json,
                },
            )

        async def replay() -> AsyncIterator[str]:
            yield final

        body = replay()

    return StreamingResponse(
        body,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
