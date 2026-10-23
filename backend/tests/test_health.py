from alembic.config import Config
from alembic.script import ScriptDirectory
from httpx import AsyncClient

from app import __version__


async def test_health_reports_database_and_applied_revision(client: AsyncClient, alembic_config: Config) -> None:
    response = await client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["environment"] == "test"
    assert body["database"]["status"] == "ok"
    assert body["database"]["migration_revision"] == ScriptDirectory.from_config(alembic_config).get_current_head()
    assert body["database"]["latency_ms"] >= 0


async def test_health_is_503_degraded_when_database_is_down(client_without_database: AsyncClient) -> None:
    response = await client_without_database.get("/api/v1/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == {"status": "unavailable", "migration_revision": None, "latency_ms": None}


async def test_request_id_is_echoed_when_valid(client_without_database: AsyncClient) -> None:
    response = await client_without_database.get("/api/v1/health", headers={"X-Request-ID": "abc-123.def_4"})

    assert response.headers["X-Request-ID"] == "abc-123.def_4"


async def test_unsafe_request_id_is_replaced(client_without_database: AsyncClient) -> None:
    response = await client_without_database.get("/api/v1/health", headers={"X-Request-ID": "bad id\r\nx: y"})

    trace_id = response.headers["X-Request-ID"]
    assert trace_id != "bad id\r\nx: y"
    assert len(trace_id) == 32 and trace_id.isalnum()


async def test_unknown_route_is_404(client_without_database: AsyncClient) -> None:
    response = await client_without_database.get("/api/v1/does-not-exist")

    assert response.status_code == 404
