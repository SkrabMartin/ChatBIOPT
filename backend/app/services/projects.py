import uuid

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.service import Actor
from app.core.errors import NotFoundError
from app.models import Conversation, Project, ProjectMember
from app.schemas.workspace import Page, ProjectCreate, ProjectRead, ProjectUpdate

_conversation_count = (
    select(func.count(Conversation.id))
    .where(Conversation.project_id == Project.id)
    .correlate(Project)
    .scalar_subquery()
    .label("conversation_count")
)


def _read(project: Project, conversation_count: int, role: str) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        title=project.title,
        description=project.description,
        conversation_count=conversation_count,
        role=role,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


class ProjectService:
    """Projects of one actor: every query joins the actor's membership. Routers authorize the role first."""

    def __init__(self, session: AsyncSession, actor: Actor) -> None:
        self.session = session
        self.actor = actor

    def _visible(self) -> Select[Project, int, str]:
        return select(Project, _conversation_count, ProjectMember.role).join(
            ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == self.actor.id)
        )

    async def list(self, *, limit: int, offset: int) -> Page[ProjectRead]:
        total = (
            await self.session.scalar(
                select(func.count()).select_from(ProjectMember).where(ProjectMember.user_id == self.actor.id)
            )
            or 0
        )
        rows = await self.session.execute(
            self._visible().order_by(Project.updated_at.desc(), Project.id).limit(limit).offset(offset)
        )
        return Page(items=[_read(p, count, role) for p, count, role in rows], total=total, limit=limit, offset=offset)

    async def get(self, project_id: uuid.UUID) -> ProjectRead:
        row = (await self.session.execute(self._visible().where(Project.id == project_id))).first()
        if row is None:
            raise NotFoundError("Project not found")
        project, count, role = row
        return _read(project, count, role)

    async def create(self, data: ProjectCreate) -> ProjectRead:
        project = Project(title=data.title, description=data.description)
        self.session.add(project)
        await self.session.flush()
        self.session.add(ProjectMember(project_id=project.id, user_id=self.actor.id, role="owner"))
        await self.session.commit()
        return _read(project, 0, "owner")

    async def update(self, project_id: uuid.UUID, data: ProjectUpdate) -> ProjectRead:
        project = await self._load(project_id)
        changes = data.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(project, field, value)
        if changes:
            await self.session.commit()
        return await self.get(project_id)

    async def delete(self, project_id: uuid.UUID) -> None:
        # Conversations and messages go with it via ON DELETE CASCADE.
        project = await self._load(project_id)
        await self.session.delete(project)
        await self.session.commit()

    async def _load(self, project_id: uuid.UUID) -> Project:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project
