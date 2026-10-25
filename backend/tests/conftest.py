import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
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
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.auth.deps import CSRF_HEADER, CSRF_VALUE
from app.auth.service import AuthService
from app.core.config import Settings
from app.db.session import create_engine
from app.main import create_app
from app.workers.ingestion import IngestionWorker, build_worker
from tests.fakes import FakeEntitySources, FakeEuropePMC, FakeNCBI, FakeOllama, FakeUpstreams

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


TEST_PASSWORD = "correct horse battery staple"


@dataclass(frozen=True)
class Account:
    id: uuid.UUID
    email: str
    password: str = TEST_PASSWORD


async def create_user(database_url: str, email: str, *, admin: bool = False) -> Account:
    settings = make_settings(database_url)
    engine = create_engine(settings)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            user = await AuthService(session, settings).create_user(
                email, email.split("@")[0].title(), TEST_PASSWORD, is_admin=admin
            )
    finally:
        await engine.dispose()
    return Account(user.id, user.email)


async def login(client: AsyncClient, user: Account) -> None:
    response = await client.post("/api/v1/auth/login", json={"email": user.email, "password": user.password})
    assert response.status_code == 200, response.text


@asynccontextmanager
async def running_app(
    settings: Settings,
    ncbi: FakeNCBI | None = None,
    ollama: FakeOllama | None = None,
    europepmc: FakeEuropePMC | None = None,
    entities: FakeEntitySources | None = None,
    user: Account | None = None,
) -> AsyncIterator[AsyncClient]:
    """A fresh app instance (own engine and pool) with its lifespan running: one simulated process start.

    Outbound HTTP always goes to fakes: tests never reach NCBI, Europe PMC, entity providers or a model server.
    The client sends the CSRF header like the web UI does and, if `user` is given, is signed in as that user.
    """
    upstreams = FakeUpstreams(ncbi or FakeNCBI(), ollama or FakeOllama(), europepmc, entities)
    app = create_app(settings, http_transport=httpx.MockTransport(upstreams.handle))
    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(
            transport=transport, base_url="http://test", headers={CSRF_HEADER: CSRF_VALUE}
        ) as client:
            if user is not None:
                await login(client, user)
            yield client


@pytest.fixture
def ncbi() -> FakeNCBI:
    return FakeNCBI()


@pytest.fixture
def ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def europepmc() -> FakeEuropePMC:
    return FakeEuropePMC()


@pytest.fixture
def entity_sources() -> FakeEntitySources:
    return FakeEntitySources()


@pytest.fixture
def object_store_path(tmp_path: Path) -> Path:
    return tmp_path / "objects"


StartApp = Callable[..., AbstractAsyncContextManager[AsyncClient]]
_DEFAULT_USER = object()


@pytest.fixture
async def user(migrated_database: str) -> Account:
    """The signed-in user of `client` and `start_app()` (owner of every project it creates)."""
    return await create_user(migrated_database, "alice@example.org")


@pytest.fixture
def start_app(
    migrated_database: str,
    ncbi: FakeNCBI,
    ollama: FakeOllama,
    europepmc: FakeEuropePMC,
    entity_sources: FakeEntitySources,
    object_store_path: Path,
    user: Account,
) -> StartApp:
    """Start an app on the migrated test DB, signed in as `user` (`as_user=` another user or None for anonymous);
    other keyword arguments override settings."""

    def start(as_user: object = _DEFAULT_USER, **overrides: Any) -> AbstractAsyncContextManager[AsyncClient]:
        return running_app(
            make_settings(migrated_database, object_store_path=object_store_path, **overrides),
            ncbi,
            ollama,
            europepmc,
            entity_sources,
            user if as_user is _DEFAULT_USER else as_user,  # type: ignore[arg-type]
        )

    return start


@pytest.fixture
async def client(start_app: StartApp) -> AsyncIterator[AsyncClient]:
    async with start_app() as c:
        yield c


@pytest.fixture
async def worker(
    migrated_database: str,
    ncbi: FakeNCBI,
    ollama: FakeOllama,
    europepmc: FakeEuropePMC,
    object_store_path: Path,
) -> AsyncIterator[IngestionWorker]:
    """The ingestion worker wired to the same DB, object store and fakes as `client` (no retry delay)."""
    settings = make_settings(migrated_database, object_store_path=object_store_path, ingestion_retry_delay_s=0)
    engine = create_engine(settings)
    transport = httpx.MockTransport(FakeUpstreams(ncbi, ollama, europepmc).handle)
    async with httpx.AsyncClient(transport=transport) as http:
        yield build_worker(settings, async_sessionmaker(engine, expire_on_commit=False), http)
    await engine.dispose()


@pytest.fixture
async def client_without_database() -> AsyncIterator[AsyncClient]:
    # Port 1 on localhost refuses connections immediately: a deterministic "database down".
    async with running_app(make_settings("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none")) as c:
        yield c
