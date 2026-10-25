"""Accounts and sessions: Argon2id password hashes, opaque session tokens (only SHA-256 stored), revocation."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ConflictError, InvalidCredentialsError, NotFoundError
from app.models import User, UserSession

_hasher = PasswordHasher()  # argon2id with the library's current (RFC 9106 low-memory) parameters
# Verified when the email is unknown, so a miss costs as much time as a wrong password (no account probing).
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))
LAST_SEEN_RESOLUTION = timedelta(minutes=1)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Actor:
    """The authenticated caller, passed to every authorization check."""

    id: uuid.UUID
    email: str
    display_name: str
    is_admin: bool
    session_id: uuid.UUID


class AuthService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings

    async def create_user(self, email: str, display_name: str, password: str, *, is_admin: bool = False) -> User:
        user = User(
            email=normalize_email(email),
            display_name=display_name.strip(),
            password_hash=hash_password(password),
            is_admin=is_admin,
        )
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("A user with that email already exists") from exc
        return user

    async def login(self, email: str, password: str) -> tuple[User, str]:
        """(user, session token). Same error for unknown email, wrong password and disabled account."""
        user = await self.session.scalar(select(User).where(User.email == normalize_email(email)))
        try:
            _hasher.verify(user.password_hash if user else _DUMMY_HASH, password)
        except (VerificationError, InvalidHashError):
            raise InvalidCredentialsError("Invalid email or password") from None
        if user is None or user.disabled:
            raise InvalidCredentialsError("Invalid email or password")
        if _hasher.check_needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(32)
        self.session.add(
            UserSession(
                user_id=user.id,
                token_sha256=_token_digest(token),
                expires_at=now + timedelta(hours=self.settings.session_ttl_hours),
                last_seen_at=now,
            )
        )
        user.last_login_at = now
        await self.session.commit()
        return user, token

    async def authenticate(self, token: str) -> Actor | None:
        """The actor for a session token, or None if unknown, revoked, expired, idle too long or user disabled."""
        now = datetime.now(UTC)
        row = (
            await self.session.execute(
                select(UserSession, User)
                .join(User, User.id == UserSession.user_id)
                .where(UserSession.token_sha256 == _token_digest(token))
            )
        ).first()
        if row is None:
            return None
        user_session, user = row
        idle_limit = user_session.last_seen_at + timedelta(hours=self.settings.session_idle_hours)
        if user_session.revoked_at or user_session.expires_at <= now or idle_limit <= now or user.disabled:
            return None
        if now - user_session.last_seen_at >= LAST_SEEN_RESOLUTION:
            user_session.last_seen_at = now
            await self.session.commit()
        return Actor(user.id, user.email, user.display_name, user.is_admin, user_session.id)

    async def logout(self, session_id: uuid.UUID) -> None:
        await self.session.execute(
            update(UserSession).where(UserSession.id == session_id).values(revoked_at=datetime.now(UTC))
        )
        await self.session.commit()

    async def revoke_all(self, user_id: uuid.UUID, *, except_session: uuid.UUID | None = None) -> None:
        statement = update(UserSession).where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        if except_session:
            statement = statement.where(UserSession.id != except_session)
        await self.session.execute(statement.values(revoked_at=datetime.now(UTC)))

    async def change_password(self, actor: Actor, current: str, new: str) -> None:
        user = await self.session.get(User, actor.id)
        if user is None:
            raise NotFoundError("User not found")
        try:
            _hasher.verify(user.password_hash, current)
        except (VerificationError, InvalidHashError):
            raise InvalidCredentialsError("Current password is incorrect") from None
        user.password_hash = hash_password(new)
        await self.revoke_all(user.id, except_session=actor.session_id)
        await self.session.commit()
