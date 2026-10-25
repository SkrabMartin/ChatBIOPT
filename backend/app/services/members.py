import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, LastOwnerError, NotFoundError
from app.models import ProjectMember, User
from app.schemas.auth import MemberAdd, MemberRead, Role


def _read(member: ProjectMember, user: User) -> MemberRead:
    return MemberRead(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=member.role,
        added_at=member.added_at,
    )


class MemberService:
    """Project memberships. Callers authorize first (owner for changes; any member to list or leave)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list(self, project_id: uuid.UUID) -> list[MemberRead]:
        rows = await self.session.execute(
            select(ProjectMember, User)
            .join(User, User.id == ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id)
            .order_by(ProjectMember.added_at, User.email)
        )
        return [_read(m, u) for m, u in rows]

    async def add(self, project_id: uuid.UUID, data: MemberAdd) -> MemberRead:
        user = await self.session.scalar(select(User).where(User.email == data.email, User.disabled.is_(False)))
        if user is None:
            raise NotFoundError("No user with that email")
        member = ProjectMember(project_id=project_id, user_id=user.id, role=data.role)
        self.session.add(member)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("Already a member of this project") from exc
        return _read(member, user)

    async def _load(self, project_id: uuid.UUID, user_id: uuid.UUID) -> tuple[ProjectMember, User]:
        row = (
            await self.session.execute(
                select(ProjectMember, User)
                .join(User, User.id == ProjectMember.user_id)
                .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)
                .with_for_update(of=ProjectMember)
            )
        ).first()
        if row is None:
            raise NotFoundError("Not a member of this project")
        return row[0], row[1]

    async def _ensure_other_owner(self, project_id: uuid.UUID, member: ProjectMember) -> None:
        if member.role != "owner":
            return
        # Lock the project's owner rows so two owners cannot demote each other concurrently.
        owners = await self.session.scalars(
            select(ProjectMember.user_id)
            .where(ProjectMember.project_id == project_id, ProjectMember.role == "owner")
            .with_for_update()
        )
        if len(owners.all()) <= 1:
            raise LastOwnerError("A project needs at least one owner: add or promote another owner first")

    async def update(self, project_id: uuid.UUID, user_id: uuid.UUID, role: Role) -> MemberRead:
        member, user = await self._load(project_id, user_id)
        if role != "owner":
            await self._ensure_other_owner(project_id, member)
        member.role = role
        await self.session.commit()
        return _read(member, user)

    async def remove(self, project_id: uuid.UUID, user_id: uuid.UUID) -> None:
        member, _ = await self._load(project_id, user_id)
        await self._ensure_other_owner(project_id, member)
        await self.session.delete(member)
        await self.session.commit()
