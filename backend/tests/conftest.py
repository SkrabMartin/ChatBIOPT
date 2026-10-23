import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.main import create_app

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


def make_settings(database_url: str) -> Settings:
    return Settings(_env_file=None, app_env="test", database_url=database_url)


async def _client_for(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def client(migrated_database: str) -> AsyncIterator[AsyncClient]:
    async for c in _client_for(make_settings(migrated_database)):
        yield c


@pytest.fixture
async def client_without_database() -> AsyncIterator[AsyncClient]:
    # Port 1 on localhost refuses connections immediately: a deterministic "database down".
    settings = make_settings("postgresql+psycopg://nobody:nothing@127.0.0.1:1/none")
    async for c in _client_for(settings):
        yield c
