import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Conversation, Message, Project
from app.schemas.workspace import (
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    MessageCreate,
    MessagePage,
    MessageRead,
    Page,
)


def _read(conversation: Conversation) -> ConversationRead:
    return ConversationRead(
        id=conversation.id,
        project_id=conversation.project_id,
        title=conversation.title,
        message_count=conversation.last_message_seq,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


async def append_message(
    session: AsyncSession, conversation_id: uuid.UUID, *, role: str, content: str, run_id: uuid.UUID | None = None
) -> Message:
    """Add a message with the next sequence number. Caller commits.

    The atomic increment row-locks the conversation until commit, serialising concurrent writers
    (user posts and AI runs alike) into unique, gap-free sequences.
    """
    sequence = await session.scalar(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(last_message_seq=Conversation.last_message_seq + 1, updated_at=func.now())
        .returning(Conversation.last_message_seq)
    )
    if sequence is None:
        raise NotFoundError("Conversation not found")
    message = Message(conversation_id=conversation_id, sequence=sequence, role=role, content=content, run_id=run_id)
    session.add(message)
    await session.flush()
    return message


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_project(self, project_id: uuid.UUID, *, limit: int, offset: int) -> Page[ConversationRead]:
        if await self.session.get(Project, project_id) is None:
            raise NotFoundError("Project not found")
        scope = Conversation.project_id == project_id
        total = await self.session.scalar(select(func.count(Conversation.id)).where(scope)) or 0
        conversations = await self.session.scalars(
            select(Conversation)
            .where(scope)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
            .limit(limit)
            .offset(offset)
        )
        return Page(items=[_read(c) for c in conversations], total=total, limit=limit, offset=offset)

    async def create(self, project_id: uuid.UUID, data: ConversationCreate) -> ConversationRead:
        if await self.session.get(Project, project_id) is None:
            raise NotFoundError("Project not found")
        conversation = Conversation(project_id=project_id, title=data.title, last_message_seq=0)
        self.session.add(conversation)
        await self.session.commit()
        return _read(conversation)

    async def get(self, conversation_id: uuid.UUID) -> ConversationRead:
        return _read(await self._load(conversation_id))

    async def update(self, conversation_id: uuid.UUID, data: ConversationUpdate) -> ConversationRead:
        conversation = await self._load(conversation_id)
        conversation.title = data.title
        await self.session.commit()
        return _read(conversation)

    async def delete(self, conversation_id: uuid.UUID) -> None:
        conversation = await self._load(conversation_id)
        await self.session.delete(conversation)
        await self.session.commit()

    async def list_messages(self, conversation_id: uuid.UUID, *, after_sequence: int, limit: int) -> MessagePage:
        await self._load(conversation_id)
        # Fetch one extra row to learn whether another page exists without a COUNT.
        messages = list(
            await self.session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation_id, Message.sequence > after_sequence)
                .order_by(Message.sequence)
                .limit(limit + 1)
            )
        )
        return MessagePage(
            items=[MessageRead.model_validate(m) for m in messages[:limit]], has_more=len(messages) > limit
        )

    async def add_message(self, conversation_id: uuid.UUID, data: MessageCreate) -> MessageRead:
        message = await append_message(self.session, conversation_id, role=data.role, content=data.content)
        await self.session.commit()
        return MessageRead.model_validate(message)

    async def _load(self, conversation_id: uuid.UUID) -> Conversation:
        conversation = await self.session.get(Conversation, conversation_id)
        if conversation is None:
            raise NotFoundError("Conversation not found")
        return conversation
