import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Conversation, Project
from app.schemas.workspace import Page, ProjectCreate, ProjectRead, ProjectUpdate

_conversation_count = (
    select(func.count(Conversation.id))
    .where(Conversation.project_id == Project.id)
    .correlate(Project)
    .scalar_subquery()
    .label("conversation_count")
)


def _read(project: Project, conversation_count: int) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        title=project.title,
        description=project.description,
        conversation_count=conversation_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, *, limit: int, offset: int) -> Page[ProjectRead]:
        total = await self.session.scalar(select(func.count(Project.id))) or 0
        rows = await self.session.execute(
            select(Project, _conversation_count)
            .order_by(Project.updated_at.desc(), Project.id)
            .limit(limit)
            .offset(offset)
        )
        return Page(items=[_read(p, count) for p, count in rows], total=total, limit=limit, offset=offset)

    async def get(self, project_id: uuid.UUID) -> ProjectRead:
        result = await self.session.execute(select(Project, _conversation_count).where(Project.id == project_id))
        row = result.first()
        if row is None:
            raise NotFoundError("Project not found")
        return _read(*row)

    async def create(self, data: ProjectCreate) -> ProjectRead:
        project = Project(title=data.title, description=data.description)
        self.session.add(project)
        await self.session.commit()
        return _read(project, 0)

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

    async def ensure_exists(self, project_id: uuid.UUID) -> None:
        await self._load(project_id)

    async def _load(self, project_id: uuid.UUID) -> Project:
        project = await self.session.get(Project, project_id)
        if project is None:
            raise NotFoundError("Project not found")
        return project
