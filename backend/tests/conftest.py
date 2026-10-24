import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.main import create_app
from tests.fakes import FakeNCBI, FakeOllama, FakeUpstreams

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Dedicated database so tests never touch development data; created on demand.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://bioresearch:bioresearch@localhost:5432/bioresearch_test"
)


def _ensure_database_exists(url: str) -> None:
    target = make_url(url)
    admin_dsn = target.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin_dsn, autocommit=True, connect_timeout=5) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target.database,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{target.database}"')


@pytest.fixture(scope="session")
def database_url() -> str:
    _ensure_database_exists(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logger"] = False
    return config


@pytest.fixture
def migrated_database(alembic_config: Config, database_url: str) -> Iterator[str]:
    command.upgrade(alembic_config, "head")
    yield database_url
    command.downgrade(alembic_config, "base")


def make_settings(database_url: str, **overrides: Any) -> Settings:
    # No backoff delay in tests; retry *behaviour* is still exercised.
    values: dict[str, Any] = {
        "app_env": "test",
        "database_url": database_url,
        "ncbi_backoff_base_s": 0,
        "ollama_base_url": "http://ollama.test:11434",
        **overrides,
    }
    return Settings(_env_file=None, **values)


@asynccontextmanager
async def running_app(
    settings: Settings, ncbi: FakeNCBI | None = None, ollama: FakeOllama | None = None
) -> AsyncIterator[AsyncClient]:
    """A fresh app instance (own engine and pool) with its lifespan running: one simulated process start.

    Outbound HTTP always goes to fakes: tests never reach NCBI or a model server.
    """
    upstreams = FakeUpstreams(ncbi or FakeNCBI(), ollama or FakeOllama())
    app = create_app(settings, http_transport=httpx.MockTransport(upstreams.handle))
    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
def ncbi() -> FakeNCBI:
    return FakeNCBI()


@pytest.fixture
def ollama() -> FakeOllama:
    return FakeOllama()


StartApp = Callable[..., AbstractAsyncContextManager[AsyncClient]]


@pytest.fixture
def start_app(migrated_database: str, ncbi: FakeNCBI, ollama: FakeOllama) -> StartApp:
    """Start an app on the migrated test DB; keyword arguments override settings."""
    return lambda **overrides: running_app(make_settings(migrated_database, **overrides), ncbi, ollama)


@pytest.fixture
async def client(start_app: StartApp) -> AsyncIterator[AsyncClient]:
    async with start_app() as c:
        yield c


@pytest.fixture
async def client_without_database() -> AsyncIterator[AsyncClient]:
    # Port 1 on localhost refuses connections immediately: a deterministic "database down".
    async with running_app(make_settings("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none")) as c:
        yield c
