import asyncio
import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app import __version__
from app.core.config import Settings, get_settings
from app.db.session import get_engine
from app.schemas.health import DatabaseHealth, HealthResponse

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


async def _probe_database(engine: AsyncEngine) -> DatabaseHealth:
    start = time.perf_counter()
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
        has_version_table = await conn.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
        revision = (
            await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1")) if has_version_table else None
        )
    return DatabaseHealth(
        status="ok", migration_revision=revision, latency_ms=round((time.perf_counter() - start) * 1000, 2)
    )


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthResponse}},
)
async def health(
    response: Response,
    engine: Annotated[AsyncEngine, Depends(get_engine)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    """Readiness: API process is up and the database answers within the configured timeout."""
    try:
        database = await asyncio.wait_for(_probe_database(engine), timeout=settings.health_check_timeout_s)
    except (SQLAlchemyError, OSError, TimeoutError) as exc:
        logger.warning("database health check failed", extra={"error_type": type(exc).__name__})
        database = DatabaseHealth(status="unavailable")

    overall = "ok" if database.status == "ok" else "degraded"
    if overall != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthResponse(status=overall, version=__version__, environment=settings.app_env, database=database)
