import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import __version__
from app.ai.gateway.router import ModelRouter
from app.ai.registry import load_registry
from app.ai.runner import RunManager
from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.db.session import create_engine
from app.integrations.pubmed.client import PubMedClient
from app.services.runs import recover_interrupted_runs
from app.storage.object_store import FilesystemObjectStore

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, *, http_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    """`http_transport` replaces the network for outbound provider/model calls (tests use `httpx.MockTransport`)."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    registry = load_registry(settings.model_registry_path)

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
        app.state.object_store = FilesystemObjectStore(settings.object_store_path)

        model_http = httpx.AsyncClient(
            transport=http_transport,
            timeout=httpx.Timeout(
                connect=settings.model_connect_timeout_s, read=settings.model_read_timeout_s, write=30, pool=5
            ),
            follow_redirects=False,
        )
        model_router = ModelRouter(
            registry,
            model_http,
            ollama_base_url=settings.ollama_base_url,
            allow_external=settings.allow_external_models,
        )
        app.state.model_router = model_router
        run_manager = RunManager(
            app.state.session_factory,
            model_router,
            max_duration_s=settings.run_max_duration_s,
            rag_top_k=settings.rag_top_k,
        )
        app.state.run_manager = run_manager
        try:
            async with app.state.session_factory() as session:
                if recovered := await recover_interrupted_runs(session):
                    logger.warning("closed interrupted runs", extra={"count": recovered})
        except (OSError, SQLAlchemyError):
            # The API still starts without a database (health reports it); recovery runs on the next start.
            logger.warning("run recovery skipped: database unavailable")

        logger.info("startup", extra={"environment": settings.app_env, "version": __version__})
        try:
            yield
        finally:
            await run_manager.shutdown()
            await model_http.aclose()
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
