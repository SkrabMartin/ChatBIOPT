import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import __version__
from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import create_engine
from app.integrations.pubmed.client import PubMedClient

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, http_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`http_transport` replaces the network for outbound provider calls (tests use `httpx.MockTransport`)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)
        user_agent = f"BioResearchAI/{__version__} ({settings.ncbi_email or 'no contact configured'})"
        http = httpx.AsyncClient(
            transport=http_transport,
            timeout=settings.ncbi_timeout_s,
            headers={"User-Agent": user_agent},
            follow_redirects=False,
        )
        app.state.pubmed = PubMedClient.from_settings(http, settings)
        logger.info("startup", extra={"environment": settings.app_env, "version": __version__})
        try:
            yield
        finally:
            await http.aclose()
            await engine.dispose()

    app = FastAPI(
        title="BioResearch AI API",
        version=__version__,
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    app.add_middleware(RequestContextMiddleware)
    app.include_router(api_router)
    register_error_handlers(app)

    return app
