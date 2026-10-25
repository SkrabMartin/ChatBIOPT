import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 256  # Argon2 accepts more; bounded so a request cannot make hashing arbitrarily expensive


def _email(value: str) -> str:
    value = value.strip().lower()
    local, sep, domain = value.partition("@")
    if not sep or not local or "." not in domain or " " in value:
        raise ValueError("not a valid email address")
    return value


Email = Annotated[str, StringConstraints(min_length=3, max_length=320), AfterValidator(_email)]
NewPassword = Annotated[str, StringConstraints(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Role = Literal["viewer", "editor", "owner"]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(_Input):
    email: Annotated[str, StringConstraints(min_length=1, max_length=320)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=PASSWORD_MAX_LENGTH)]


class PasswordChange(_Input):
    current_password: Annotated[str, StringConstraints(min_length=1, max_length=PASSWORD_MAX_LENGTH)]
    new_password: NewPassword


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str
    is_admin: bool
    created_at: datetime


class AdminUserRead(UserRead):
    disabled: bool
    last_login_at: datetime | None


class UserCreate(_Input):
    email: Email
    display_name: DisplayName
    password: NewPassword
    is_admin: bool = False


class UserUpdate(_Input):
    display_name: DisplayName | None = None
    password: NewPassword | None = None
    is_admin: bool | None = None
    disabled: bool | None = None


class MemberRead(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: Role
    added_at: datetime


class MemberAdd(_Input):
    email: Email
    role: Role = Field(default="viewer")


class MemberUpdate(_Input):
    role: Role
