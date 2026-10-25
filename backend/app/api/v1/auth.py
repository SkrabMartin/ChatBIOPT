import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import CurrentActor, check_csrf, require_admin
from app.auth.service import Actor, AuthService, hash_password, normalize_email
from app.core.config import Settings, get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.rate_limit import SlidingWindowLimiter
from app.db.session import get_session
from app.models import User
from app.schemas.auth import AdminUserRead, LoginRequest, PasswordChange, UserCreate, UserRead, UserUpdate

Session = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Public: the only routes reachable without a session (besides /health).
public_router = APIRouter(prefix="/auth", tags=["auth"])
router = APIRouter(prefix="/auth", tags=["auth"])
admin_router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


def _set_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=int(settings.session_ttl_hours * 3600),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


# CSRF check as a dependency so it runs before body validation; it also blocks login CSRF
# (signing a victim's browser into the attacker's account).
@public_router.post("/login", response_model=UserRead, dependencies=[Depends(check_csrf)])
async def login(
    data: LoginRequest, request: Request, response: Response, session: Session, settings: SettingsDep
) -> UserRead:
    """Start a session (HttpOnly cookie). Limited per client IP and per email against password guessing."""
    limiter: SlidingWindowLimiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else "unknown"
    limiter.check(f"login-ip:{client_ip}", settings.rate_limit_login_per_minute, "sign-in")
    limiter.check(f"login-email:{normalize_email(data.email)}", settings.rate_limit_login_per_minute, "sign-in")
    user, token = await AuthService(session, settings).login(data.email, data.password)
    _set_cookie(response, settings, token)
    return UserRead.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(actor: CurrentActor, session: Session, settings: SettingsDep) -> Response:
    await AuthService(session, settings).logout(actor.session_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@router.get("/me", response_model=UserRead)
async def me(actor: CurrentActor, session: Session) -> UserRead:
    user = await session.get(User, actor.id)
    return UserRead.model_validate(user)


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: PasswordChange, actor: CurrentActor, session: Session, settings: SettingsDep
) -> Response:
    """Change your password; every other session of yours is signed out."""
    await AuthService(session, settings).change_password(actor, data.current_password, data.new_password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@admin_router.get("/users", response_model=list[AdminUserRead])
async def list_users(session: Session) -> list[AdminUserRead]:
    return [AdminUserRead.model_validate(u) for u in await session.scalars(select(User).order_by(User.email))]


@admin_router.post("/users", response_model=AdminUserRead, status_code=status.HTTP_201_CREATED)
async def create_user(data: UserCreate, session: Session, settings: SettingsDep) -> AdminUserRead:
    user = await AuthService(session, settings).create_user(
        data.email, data.display_name, data.password, is_admin=data.is_admin
    )
    return AdminUserRead.model_validate(user)


@admin_router.patch("/users/{user_id}", response_model=AdminUserRead)
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    session: Session,
    settings: SettingsDep,
    admin: Annotated[Actor, Depends(require_admin)],
) -> AdminUserRead:
    """Rename, reset the password, grant/revoke admin, disable/enable. Disabling or a reset signs the user out."""
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFoundError("User not found")
    if user.id == admin.id and (data.disabled or data.is_admin is False):
        raise ConflictError("You cannot disable or demote your own account")
    auth = AuthService(session, settings)
    if data.display_name is not None:
        user.display_name = data.display_name
    if data.is_admin is not None:
        user.is_admin = data.is_admin
    if data.disabled is not None:
        user.disabled = data.disabled
    if data.password is not None:
        user.password_hash = hash_password(data.password)
    if data.disabled or data.password is not None:
        await auth.revoke_all(user.id)
    await session.commit()
    return AdminUserRead.model_validate(user)
