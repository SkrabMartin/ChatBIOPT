import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentActor, authorize_project, requires
from app.db.session import get_session
from app.schemas.auth import MemberAdd, MemberRead, MemberUpdate
from app.schemas.workspace import (
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    MessageCreate,
    MessagePage,
    MessageRead,
    Page,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
)
from app.services.conversations import ConversationService
from app.services.members import MemberService
from app.services.projects import ProjectService

router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def project_service(session: Session, actor: CurrentActor) -> ProjectService:
    return ProjectService(session, actor)


def conversation_service(session: Session) -> ConversationService:
    return ConversationService(session)


Projects = Annotated[ProjectService, Depends(project_service)]
Conversations = Annotated[ConversationService, Depends(conversation_service)]
ProjectViewer = [Depends(requires("project_id", "viewer"))]
ProjectEditor = [Depends(requires("project_id", "editor"))]
ProjectOwner = [Depends(requires("project_id", "owner"))]
ConversationViewer = [Depends(requires("conversation_id", "viewer"))]
ConversationEditor = [Depends(requires("conversation_id", "editor"))]


@router.get("/projects", response_model=Page[ProjectRead], tags=["projects"])
async def list_projects(projects: Projects, limit: Limit = 50, offset: Offset = 0) -> Page[ProjectRead]:
    return await projects.list(limit=limit, offset=offset)


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED, tags=["projects"])
async def create_project(data: ProjectCreate, projects: Projects) -> ProjectRead:
    return await projects.create(data)


@router.get("/projects/{project_id}", response_model=ProjectRead, tags=["projects"], dependencies=ProjectViewer)
async def get_project(project_id: uuid.UUID, projects: Projects) -> ProjectRead:
    return await projects.get(project_id)


@router.patch("/projects/{project_id}", response_model=ProjectRead, tags=["projects"], dependencies=ProjectOwner)
async def update_project(project_id: uuid.UUID, data: ProjectUpdate, projects: Projects) -> ProjectRead:
    return await projects.update(project_id, data)


@router.delete(
    "/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["projects"], dependencies=ProjectOwner
)
async def delete_project(project_id: uuid.UUID, projects: Projects) -> Response:
    await projects.delete(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/projects/{project_id}/conversations",
    response_model=Page[ConversationRead],
    tags=["conversations"],
    dependencies=ProjectViewer,
)
async def list_conversations(
    project_id: uuid.UUID, conversations: Conversations, limit: Limit = 50, offset: Offset = 0
) -> Page[ConversationRead]:
    return await conversations.list_for_project(project_id, limit=limit, offset=offset)


@router.post(
    "/projects/{project_id}/conversations",
    response_model=ConversationRead,
    status_code=status.HTTP_201_CREATED,
    tags=["conversations"],
    dependencies=ProjectEditor,
)
async def create_conversation(
    project_id: uuid.UUID, data: ConversationCreate, conversations: Conversations
) -> ConversationRead:
    return await conversations.create(project_id, data)


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationRead,
    tags=["conversations"],
    dependencies=ConversationViewer,
)
async def get_conversation(conversation_id: uuid.UUID, conversations: Conversations) -> ConversationRead:
    return await conversations.get(conversation_id)


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationRead,
    tags=["conversations"],
    dependencies=ConversationEditor,
)
async def update_conversation(
    conversation_id: uuid.UUID, data: ConversationUpdate, conversations: Conversations
) -> ConversationRead:
    return await conversations.update(conversation_id, data)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["conversations"],
    dependencies=ConversationEditor,
)
async def delete_conversation(conversation_id: uuid.UUID, conversations: Conversations) -> Response:
    await conversations.delete(conversation_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=MessagePage,
    tags=["messages"],
    dependencies=ConversationViewer,
)
async def list_messages(
    conversation_id: uuid.UUID,
    conversations: Conversations,
    after_sequence: Annotated[int, Query(ge=0, description="Return messages with a higher sequence")] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> MessagePage:
    return await conversations.list_messages(conversation_id, after_sequence=after_sequence, limit=limit)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=MessageRead,
    status_code=status.HTTP_201_CREATED,
    tags=["messages"],
    dependencies=ConversationEditor,
)
async def create_message(
    conversation_id: uuid.UUID, data: MessageCreate, conversations: Conversations, actor: CurrentActor
) -> MessageRead:
    return await conversations.add_message(conversation_id, data, actor)


def member_service(session: Session) -> MemberService:
    return MemberService(session)


Members = Annotated[MemberService, Depends(member_service)]


@router.get(
    "/projects/{project_id}/members", response_model=list[MemberRead], tags=["members"], dependencies=ProjectViewer
)
async def list_members(project_id: uuid.UUID, members: Members) -> list[MemberRead]:
    return await members.list(project_id)


@router.post(
    "/projects/{project_id}/members",
    response_model=MemberRead,
    status_code=status.HTTP_201_CREATED,
    tags=["members"],
    dependencies=ProjectOwner,
)
async def add_member(project_id: uuid.UUID, data: MemberAdd, members: Members) -> MemberRead:
    """Give an existing user access (owner only)."""
    return await members.add(project_id, data)


@router.patch(
    "/projects/{project_id}/members/{user_id}", response_model=MemberRead, tags=["members"], dependencies=ProjectOwner
)
async def update_member(project_id: uuid.UUID, user_id: uuid.UUID, data: MemberUpdate, members: Members) -> MemberRead:
    """Change a member's role (owner only). The last owner cannot be demoted."""
    return await members.update(project_id, user_id, data.role)


@router.delete(
    "/projects/{project_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["members"],
    dependencies=ProjectViewer,
)
async def remove_member(
    project_id: uuid.UUID, user_id: uuid.UUID, members: Members, session: Session, actor: CurrentActor
) -> Response:
    """Remove a member (owner), or leave the project (any member, for themself). The last owner cannot leave."""
    if user_id != actor.id:
        await authorize_project(session, actor, project_id, "owner")
    await members.remove(project_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
