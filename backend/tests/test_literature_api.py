from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import httpx
import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy.engine import make_url

from tests.fakes import FIXTURE_PMIDS, FakeNCBI, esearch, raises, status

SEARCH = "/api/v1/literature/search"


def db_rows(database_url: str, sql: str) -> list[tuple[Any, ...]]:
    dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn) as conn:
        return conn.execute(sql).fetchall()


async def test_search_returns_normalized_records_with_provenance(client: AsyncClient) -> None:
    response = await client.post(SEARCH, json={"query": "  TP53 glioblastoma ", "limit": 4})

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "pubmed"
    assert body["query"] == "TP53 glioblastoma"
    assert body["total_count"] == 1093
    assert [r["pmid"] for r in body["items"]] == FIXTURE_PMIDS, "provider ranking must be preserved"
    first = body["items"][0]
    assert first["source_url"] == f"https://pubmed.ncbi.nlm.nih.gov/{first['pmid']}/"
    assert first["retrieved_at"] == body["retrieved_at"]
    assert len(first["raw_payload_sha256"]) == 64
    assert first["normalizer_version"] == "pubmed-v1"


async def test_search_persists_sources_identifiers_and_audit_row(client: AsyncClient, migrated_database: str) -> None:
    body = (await client.post(SEARCH, json={"query": "TP53 glioblastoma", "limit": 4})).json()

    sources = db_rows(migrated_database, "SELECT external_id, raw_payload_sha256, raw_payload FROM sources")
    assert sorted(s[0] for s in sources) == sorted(FIXTURE_PMIDS)
    by_pmid = {r["pmid"]: r for r in body["items"]}
    for pmid, checksum, raw in sources:
        assert checksum == by_pmid[pmid]["raw_payload_sha256"]
        assert f'<PMID Version="1">{pmid}</PMID>' in raw

    identifiers = db_rows(
        migrated_database,
        "SELECT i.scheme, i.value FROM source_identifiers i JOIN sources s ON s.id = i.source_id "
        "WHERE s.external_id = '18772890' ORDER BY i.scheme",
    )
    assert identifiers == [("doi", "10.1038/nature07385"), ("pmcid", "PMC2671642"), ("pmid", "18772890")]

    searches = db_rows(migrated_database, "SELECT id, query, total_count, result_external_ids FROM literature_searches")
    assert [(str(s[0]), s[1], s[2], s[3]) for s in searches] == [
        (body["search_id"], "TP53 glioblastoma", 1093, FIXTURE_PMIDS)
    ]


async def test_repeated_search_updates_snapshots_without_duplicating(
    client: AsyncClient, migrated_database: str
) -> None:
    await client.post(SEARCH, json={"query": "TP53 glioblastoma", "limit": 4})
    first_seen = dict(db_rows(migrated_database, "SELECT external_id, first_retrieved_at FROM sources"))

    await client.post(SEARCH, json={"query": "TP53 glioblastoma", "limit": 4})

    rows = db_rows(migrated_database, "SELECT external_id, first_retrieved_at, retrieved_at FROM sources")
    assert len(rows) == len(FIXTURE_PMIDS)
    for pmid, first_retrieved_at, retrieved_at in rows:
        assert first_retrieved_at == first_seen[pmid]
        assert retrieved_at > first_retrieved_at
    assert db_rows(migrated_database, "SELECT count(*) FROM literature_searches") == [(2,)]


async def test_empty_result_is_a_successful_search(client: AsyncClient, ncbi: FakeNCBI) -> None:
    ncbi.queue("esearch", esearch([], count=0))

    response = await client.post(SEARCH, json={"query": "zzzz no such thing"})

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total_count"] == 0
    assert ncbi.calls("efetch") == [], "no fetch when there is nothing to fetch"


@pytest.mark.parametrize(
    ("responders", "status_code", "code"),
    [
        ([raises(httpx.ReadTimeout("slow"))] * 3, 504, "upstream_timeout"),
        ([status(503)] * 3, 502, "upstream_unavailable"),
        ([status(400)], 502, "upstream_unavailable"),
    ],
    ids=["timeout", "outage", "rejected"],
)
async def test_provider_failure_is_an_error_not_an_empty_result(
    client: AsyncClient,
    ncbi: FakeNCBI,
    migrated_database: str,
    responders: list[Any],
    status_code: int,
    code: str,
) -> None:
    ncbi.queue("efetch", *responders)

    response = await client.post(SEARCH, json={"query": "TP53"})

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert "items" not in response.json()
    assert db_rows(migrated_database, "SELECT count(*) FROM literature_searches") == [(0,)]
    assert db_rows(migrated_database, "SELECT count(*) FROM sources") == [(0,)]


@pytest.mark.parametrize(
    "payload",
    [
        {"query": ""},
        {"query": "   "},
        {"query": "x" * 501},
        {"query": "q", "limit": 51},
        {"query": "q", "limit": 0},
        {"query": "q", "offset": 9990, "limit": 20},
        {"query": "q", "sort": "date"},
    ],
    ids=["empty", "blank", "too-long", "limit-high", "limit-zero", "beyond-window", "unknown-field"],
)
async def test_invalid_search_is_rejected_before_calling_pubmed(
    client: AsyncClient, ncbi: FakeNCBI, payload: dict[str, Any]
) -> None:
    response = await client.post(SEARCH, json=payload)

    assert response.status_code == 422
    assert ncbi.requests == []


async def test_detail_serves_stored_snapshot_without_calling_pubmed(client: AsyncClient, ncbi: FakeNCBI) -> None:
    searched = (await client.post(SEARCH, json={"query": "TP53 glioblastoma", "limit": 4})).json()
    calls_before = len(ncbi.requests)

    detail = await client.get("/api/v1/literature/pubmed/18772890")

    assert detail.status_code == 200
    assert detail.json() == next(r for r in searched["items"] if r["pmid"] == "18772890")
    assert len(ncbi.requests) == calls_before


async def test_detail_fetches_unknown_record_and_refresh_forces_upstream(client: AsyncClient, ncbi: FakeNCBI) -> None:
    first = await client.get("/api/v1/literature/pubmed/42906764")
    refreshed = await client.get("/api/v1/literature/pubmed/42906764", params={"refresh": "true"})

    assert first.status_code == refreshed.status_code == 200
    assert len(ncbi.calls("efetch")) == 2
    assert refreshed.json()["retrieved_at"] > first.json()["retrieved_at"]


async def test_detail_for_pmid_pubmed_does_not_know_is_404(client: AsyncClient) -> None:
    response = await client.get("/api/v1/literature/pubmed/99999999")

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "PubMed has no record with PMID 99999999"


@pytest.mark.parametrize("path", ["pubmed/12ab", "pubmed/0123", "europepmc/123"])
async def test_detail_rejects_bad_source_or_identifier(client: AsyncClient, ncbi: FakeNCBI, path: str) -> None:
    response = await client.get(f"/api/v1/literature/{path}")

    assert response.status_code == 422
    assert ncbi.requests == []


async def test_stored_records_survive_restart(
    start_app: Callable[[], AbstractAsyncContextManager[AsyncClient]], ncbi: FakeNCBI
) -> None:
    async with start_app() as first:
        await first.post(SEARCH, json={"query": "TP53 glioblastoma", "limit": 4})
    calls = len(ncbi.requests)

    async with start_app() as second:
        detail = await second.get("/api/v1/literature/pubmed/18772890")

    assert detail.status_code == 200
    assert len(ncbi.requests) == calls
