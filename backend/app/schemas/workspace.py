import uuid
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.workspace import DESCRIPTION_MAX_LENGTH, MESSAGE_MAX_LENGTH, TITLE_MAX_LENGTH

Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)]
# Blank descriptions are stored as NULL so "no description" has a single representation.
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=DESCRIPTION_MAX_LENGTH),
    AfterValidator(lambda value: value or None),
]


class _Input(BaseModel):
    # Unknown fields are rejected so client typos don't silently become no-ops.
    model_config = ConfigDict(extra="forbid")


def _reject_explicit_null(model: BaseModel, *fields: str) -> None:
    for name in fields:
        if name in model.model_fields_set and getattr(model, name) is None:
            raise ValueError(f"{name} cannot be null")


class ProjectCreate(_Input):
    title: Title
    description: Description | None = None


class ProjectUpdate(_Input):
    """Partial update: omitted fields are unchanged; `description: null` clears it."""

    title: Title | None = None
    description: Description | None = None

    @model_validator(mode="after")
    def _title_not_null(self) -> Self:
        _reject_explicit_null(self, "title")
        return self


class ProjectRead(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    conversation_count: int
    # The caller's role in this project.
    role: Literal["viewer", "editor", "owner"]
    created_at: datetime
    updated_at: datetime


class ConversationCreate(_Input):
    title: Title


class ConversationUpdate(_Input):
    title: Title


class ConversationRead(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    message_count: int
    created_at: datetime
    updated_at: datetime


class MessageCreate(_Input):
    # Clients may only author user messages; assistant messages are produced server-side by AI runs (M04).
    role: Literal["user"] = "user"
    content: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)

    @model_validator(mode="after")
    def _content_not_blank(self) -> Self:
        if not self.content.strip():
            raise ValueError("content cannot be blank")
        return self


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    sequence: int
    role: Literal["user", "assistant", "system"]
    content: str
    # The AI run that produced an assistant message; null for user messages.
    run_id: uuid.UUID | None
    # Who wrote a user message; null for assistant messages and messages from before accounts existed.
    author_id: uuid.UUID | None
    author_name: str | None = None
    created_at: datetime


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class MessagePage(BaseModel):
    items: list[MessageRead]
    has_more: bool
