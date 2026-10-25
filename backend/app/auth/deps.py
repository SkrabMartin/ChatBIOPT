"""Request authentication, CSRF defence, project authorization and per-user rate limits (FastAPI dependencies).

Authorization model: a project is visible only to its members (`project_members`); every project-owned resource
(conversation, run, claim, ingestion job) is authorized through its project. Library sources, documents and chunks
are shared between projects, so they are readable only if linked to a project the caller is a member of.
Non-members get 404 (a resource they cannot see does not exist for them); members lacking the role get 403.
Admins manage accounts; being admin grants no access to other users' projects.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from fastapi import Depends, Request
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import Actor, AuthService
from app.core.config import Settings, get_settings
from app.core.errors import CsrfError, ForbiddenError, NotFoundError, UnauthenticatedError
from app.core.rate_limit import SlidingWindowLimiter
from app.db.session import get_session
from app.models import Chunk, Claim, Conversation, Document, IngestionJob, ProjectMember, ProjectSource, Run

Role = Literal["viewer", "editor", "owner"]
RANK: dict[str, int] = {"viewer": 1, "editor": 2, "owner": 3}
CSRF_HEADER = "X-Requested-With"
CSRF_VALUE = "BioResearchAI"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

Session = Annotated[AsyncSession, Depends(get_session)]


def check_csrf(request: Request) -> None:
    """Cookie-authenticated state changes need a header a cross-site form or image cannot send.

    Browsers only add custom headers to cross-origin requests after a CORS preflight, which this API never
    approves; together with the SameSite=Lax cookie this blocks cross-site request forgery.
    """
    if request.method not in SAFE_METHODS and request.headers.get(CSRF_HEADER) != CSRF_VALUE:
        raise CsrfError(f"Missing {CSRF_HEADER}: {CSRF_VALUE} header")


async def current_actor(
    request: Request, session: Session, settings: Annotated[Settings, Depends(get_settings)]
) -> Actor:
    check_csrf(request)
    token = request.cookies.get(settings.session_cookie_name)
    actor = await AuthService(session, settings).authenticate(token) if token else None
    if actor is None:
        raise UnauthenticatedError("Sign in required")
    request.state.actor = actor
    return actor


CurrentActor = Annotated[Actor, Depends(current_actor)]


async def require_admin(actor: CurrentActor) -> Actor:
    if not actor.is_admin:
        raise ForbiddenError("Administrator role required")
    return actor


async def project_role(session: AsyncSession, actor: Actor, project_id: uuid.UUID) -> str | None:
    return await session.scalar(
        select(ProjectMember.role).where(ProjectMember.project_id == project_id, ProjectMember.user_id == actor.id)
    )


async def authorize_project(session: AsyncSession, actor: Actor, project_id: uuid.UUID, need: Role) -> str:
    """The caller's role in the project; 404 for non-members, 403 if the role is below `need`."""
    role = await project_role(session, actor, project_id)
    if role is None:
        raise NotFoundError("Project not found")
    if RANK[role] < RANK[need]:
        raise ForbiddenError(f"This action needs the {need} role in the project (you are {role})")
    return role


Resolver = Callable[[AsyncSession, uuid.UUID], Awaitable[uuid.UUID | None]]


async def _conversation_project(session: AsyncSession, conversation_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(select(Conversation.project_id).where(Conversation.id == conversation_id))


async def _run_project(session: AsyncSession, run_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(
        select(Conversation.project_id).join(Run, Run.conversation_id == Conversation.id).where(Run.id == run_id)
    )


async def _claim_project(session: AsyncSession, claim_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(select(Claim.project_id).where(Claim.id == claim_id))


async def _job_project(session: AsyncSession, job_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(select(IngestionJob.project_id).where(IngestionJob.id == job_id))


async def _direct(_: AsyncSession, project_id: uuid.UUID) -> uuid.UUID | None:
    return project_id


# Path parameter -> (resource name for errors, resolver to its project).
_PROJECT_SCOPED: dict[str, tuple[str, Resolver]] = {
    "project_id": ("Project", _direct),
    "conversation_id": ("Conversation", _conversation_project),
    "run_id": ("Run", _run_project),
    "claim_id": ("Claim", _claim_project),
    "job_id": ("Ingestion job", _job_project),
}


def requires(param: str, need: Role) -> Callable[..., Awaitable[str | None]]:
    """Dependency: the caller has at least `need` in the project owning the resource named by path `param`."""
    resource, resolver = _PROJECT_SCOPED[param]

    async def dependency(request: Request, actor: CurrentActor, session: Session) -> str | None:
        try:
            resource_id = uuid.UUID(request.path_params[param])
        except ValueError:
            return None  # not an ID at all: the route's own path validation answers 422; the handler never runs
        project_id = await resolver(session, resource_id)
        role = await project_role(session, actor, project_id) if project_id else None
        if role is None:
            raise NotFoundError(f"{resource} not found")
        if RANK[role] < RANK[need]:
            raise ForbiddenError(f"This action needs the {need} role in the project (you are {role})")
        return role

    dependency.__authz__ = (param, need)  # type: ignore[attr-defined]  # read by the route audit test
    return dependency


def _member_sources(actor: Actor) -> Any:
    return (
        select(ProjectSource.source_id)
        .join(ProjectMember, ProjectMember.project_id == ProjectSource.project_id)
        .where(ProjectMember.user_id == actor.id)
    )


async def _source_visible(session: AsyncSession, actor: Actor, source_id: uuid.UUID | None) -> bool:
    if source_id is None:
        return False
    return bool(
        await session.scalar(select(exists(_member_sources(actor).where(ProjectSource.source_id == source_id))))
    )


def requires_library(param: Literal["source_id", "document_id", "chunk_id"]) -> Callable[..., Awaitable[None]]:
    """Dependency: the shared source behind this source/document/chunk is in one of the caller's projects."""
    resource = {"source_id": "Source", "document_id": "Document", "chunk_id": "Chunk"}[param]

    async def dependency(request: Request, actor: CurrentActor, session: Session) -> None:
        try:
            resource_id = uuid.UUID(request.path_params[param])
        except ValueError:
            return  # the route's own path validation answers 422
        if param == "source_id":
            source_id: uuid.UUID | None = resource_id
        elif param == "document_id":
            source_id = await session.scalar(select(Document.source_id).where(Document.id == resource_id))
        else:
            source_id = await session.scalar(
                select(Document.source_id).join(Chunk, Chunk.document_id == Document.id).where(Chunk.id == resource_id)
            )
        if not await _source_visible(session, actor, source_id):
            raise NotFoundError(f"{resource} not found")

    dependency.__authz__ = (param, "viewer")  # type: ignore[attr-defined]
    return dependency


def rate_limit(bucket: Literal["model", "provider"]) -> Callable[..., Awaitable[None]]:
    """Dependency: per-user requests per minute for expensive operations (model calls, external providers)."""

    async def dependency(request: Request, actor: CurrentActor) -> None:
        settings: Settings = request.app.state.settings
        limiter: SlidingWindowLimiter = request.app.state.rate_limiter
        limit = settings.rate_limit_model_per_minute if bucket == "model" else settings.rate_limit_provider_per_minute
        what = "model" if bucket == "model" else "external-provider"
        limiter.check(f"{bucket}:{actor.id}", limit, what)

    dependency.__rate_limit__ = bucket  # type: ignore[attr-defined]
    return dependency
