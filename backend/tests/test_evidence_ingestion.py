"""End-to-end ingestion: API enqueues, the worker (same code as the Docker worker) processes, API reads back."""

from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from httpx import AsyncClient
from sqlalchemy.engine import make_url

from app.ingestion.chunking import CHUNKER_VERSION
from app.storage.object_store import sha256_hex
from app.workers.ingestion import IngestionWorker
from tests.conftest import StartApp
from tests.fakes import OA_PMCID, OA_PMID, FakeEuropePMC, FakeNCBI, raises, status


def db(database_url: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as conn:
        cursor = conn.execute(sql, params)  # type: ignore[arg-type]
        return cursor.fetchall() if cursor.description else []


async def new_project(client: AsyncClient) -> str:
    return str((await client.post("/api/v1/projects", json={"title": "Evidence"})).json()["id"])


async def add(client: AsyncClient, project_id: str, pmid: str) -> dict[str, Any]:
    response = await client.post(f"/api/v1/projects/{project_id}/sources", json={"identifier": pmid})
    assert response.status_code == 202, response.text
    return dict(response.json())


async def drain(worker: IngestionWorker) -> int:
    processed = 0
    while await worker.run_once():
        processed += 1
    return processed


async def library(client: AsyncClient, project_id: str) -> dict[str, Any]:
    return dict((await client.get(f"/api/v1/projects/{project_id}/sources")).json())


async def test_open_access_article_is_ingested_with_abstract_full_text_and_chunks(
    client: AsyncClient, worker: IngestionWorker, object_store_path: Path
) -> None:
    project_id = await new_project(client)

    queued = await add(client, project_id, OA_PMID)
    assert (queued["created"], queued["job"]["status"]) == (True, "queued")
    assert await drain(worker) == 1

    job = (await client.get(f"/api/v1/ingestion-jobs/{queued['job']['id']}")).json()
    assert job["status"] == "succeeded"
    assert [(s["step"], s["status"]) for s in job["steps"]] == [
        ("metadata", "done"),
        ("abstract", "done"),
        ("open_access", "done"),
        ("fulltext", "done"),
        ("verify", "done"),
    ]

    item = (await library(client, project_id))["items"][0]
    assert item["source"]["external_id"] == OA_PMID
    assert item["source"]["pmcid"] == OA_PMCID
    documents = {d["kind"]: d for d in item["documents"]}
    assert documents["abstract"]["access"] == "metadata" and documents["abstract"]["license"] is None
    assert documents["fulltext"]["access"] == "open_access"
    assert documents["fulltext"]["license"] == "CC-BY"
    assert documents["fulltext"]["license_url"] == "http://creativecommons.org/licenses/by/3.0/"
    assert documents["fulltext"]["origin_url"].endswith(f"/{OA_PMCID}/fullTextXML")
    assert all(item["chunk_counts"][d["id"]] > 0 for d in item["documents"])

    full = (await client.get(f"/api/v1/documents/{documents['fulltext']['id']}")).json()
    assert full["is_latest"] is True and full["chunker_version"] == CHUNKER_VERSION
    assert sha256_hex(full["text"].encode()) == full["text_sha256"]
    for chunk in full["chunks"]:
        assert full["text"][chunk["start_offset"] : chunk["end_offset"]] == chunk["text"]
    assert {"introduction", "methods", "results", "discussion"} <= {c["section"] for c in full["chunks"]}
    assert (object_store_path / "raw").exists(), "raw upstream payload kept for audit"

    chunk = (await client.get(f"/api/v1/chunks/{full['chunks'][0]['id']}")).json()
    assert chunk["document"]["id"] == full["id"]
    assert chunk["source"]["external_id"] == OA_PMID


async def test_reingesting_unchanged_source_is_idempotent(
    client: AsyncClient, worker: IngestionWorker, migrated_database: str
) -> None:
    project_id = await new_project(client)
    await add(client, project_id, OA_PMID)
    await drain(worker)
    counts = db(migrated_database, "SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM chunks)")
    source_id = (await library(client, project_id))["items"][0]["source"]["id"]

    refresh = await client.post(f"/api/v1/projects/{project_id}/sources/{source_id}/refresh")
    await drain(worker)

    job = (await client.get(f"/api/v1/ingestion-jobs/{refresh.json()['job']['id']}")).json()
    assert job["status"] == "succeeded"
    assert {s["step"]: s["status"] for s in job["steps"]}["abstract"] == "unchanged"
    assert {s["step"]: s["status"] for s in job["steps"]}["fulltext"] == "unchanged"
    assert db(migrated_database, "SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM chunks)") == counts


async def test_changed_abstract_creates_a_new_version_and_keeps_the_old_one(
    client: AsyncClient, worker: IngestionWorker, ncbi: FakeNCBI
) -> None:
    project_id = await new_project(client)
    await add(client, project_id, OA_PMID)
    await drain(worker)
    item = (await library(client, project_id))["items"][0]
    v1 = next(d for d in item["documents"] if d["kind"] == "abstract")
    v1_doc = (await client.get(f"/api/v1/documents/{v1['id']}")).json()

    ncbi.efetch_transform = lambda xml: xml.replace(b"Gliosarcoma is a rare", b"Gliosarcoma is an uncommon", 1)
    await client.post(f"/api/v1/projects/{project_id}/sources/{item['source']['id']}/refresh")
    await drain(worker)

    versions = (await client.get(f"/api/v1/sources/{item['source']['id']}/documents")).json()
    abstracts = [d for d in versions if d["kind"] == "abstract"]
    assert [d["version"] for d in abstracts] == [2, 1]
    old = (await client.get(f"/api/v1/documents/{v1['id']}")).json()
    assert old["is_latest"] is False
    assert old["text"] == v1_doc["text"], "the superseded version is still served unchanged"
    assert [c["id"] for c in old["chunks"]] == [c["id"] for c in v1_doc["chunks"]]
    new = (await client.get(f"/api/v1/documents/{abstracts[0]['id']}")).json()
    assert "uncommon" in new["text"] and "uncommon" not in old["text"]


@pytest.mark.parametrize(
    ("pmid", "reason"),
    [("18772890", "no explicit open license"), ("42857877", "not in the open-access subset")],
    ids=["oa-without-license", "not-open-access"],
)
async def test_full_text_is_not_stored_without_an_allowed_license(
    client: AsyncClient, worker: IngestionWorker, europepmc: FakeEuropePMC, pmid: str, reason: str
) -> None:
    project_id = await new_project(client)

    await add(client, project_id, pmid)
    await drain(worker)

    item = (await library(client, project_id))["items"][0]
    assert [d["kind"] for d in item["documents"]] == ["abstract"]
    steps = {s["step"]: s for s in item["latest_job"]["steps"]}
    assert steps["fulltext"]["status"] == "skipped"
    assert reason in steps["fulltext"]["detail"]
    assert europepmc.calls("fulltext") == [], "full text is never even downloaded"


async def test_deployment_license_allowlist_is_enforced(start_app: StartApp, worker: IngestionWorker) -> None:
    # The worker decides; restrict it to CC0 only.
    worker._pipeline.allowed_licenses = frozenset({"CC0"})
    async with start_app() as client:
        project_id = await new_project(client)
        await add(client, project_id, OA_PMID)
        await drain(worker)
        item = (await library(client, project_id))["items"][0]

    assert [d["kind"] for d in item["documents"]] == ["abstract"]
    assert "CC-BY is not allowed" in {s["step"]: s for s in item["latest_job"]["steps"]}["fulltext"]["detail"]


async def test_adding_the_same_source_twice_returns_the_active_job(client: AsyncClient) -> None:
    project_id = await new_project(client)

    first = await add(client, project_id, OA_PMID)
    second = await add(client, project_id, OA_PMID)

    assert second["created"] is False
    assert second["job"]["id"] == first["job"]["id"]


async def test_transient_provider_failure_is_retried_then_succeeds(
    client: AsyncClient, worker: IngestionWorker, europepmc: FakeEuropePMC
) -> None:
    europepmc.queue("search", *[status(503)] * 3)  # one full attempt's worth of HTTP retries
    project_id = await new_project(client)
    queued = await add(client, project_id, OA_PMID)

    assert await worker.run_once() is True
    after_first = (await client.get(f"/api/v1/ingestion-jobs/{queued['job']['id']}")).json()
    assert (after_first["status"], after_first["attempts"]) == ("queued", 1)
    assert "will retry" in after_first["error_message"]
    assert (await library(client, project_id))["items"][0]["documents"], "abstract from the first attempt is kept"

    await drain(worker)
    final = (await client.get(f"/api/v1/ingestion-jobs/{queued['job']['id']}")).json()
    assert (final["status"], final["attempts"], final["error_code"]) == ("succeeded", 2, None)


async def test_retries_are_bounded(client: AsyncClient, worker: IngestionWorker, europepmc: FakeEuropePMC) -> None:
    europepmc.queue("search", *[raises(httpx.ConnectError("down"))] * 9)
    project_id = await new_project(client)
    queued = await add(client, project_id, OA_PMID)

    assert await drain(worker) == 3
    job = (await client.get(f"/api/v1/ingestion-jobs/{queued['job']['id']}")).json()
    assert (job["status"], job["attempts"], job["error_code"]) == ("failed", 3, "upstream_unavailable")


async def test_unknown_pmid_fails_permanently_without_retry(client: AsyncClient, worker: IngestionWorker) -> None:
    project_id = await new_project(client)
    queued = await add(client, project_id, "99999999")

    assert await drain(worker) == 1
    job = (await client.get(f"/api/v1/ingestion-jobs/{queued['job']['id']}")).json()
    lib = await library(client, project_id)
    assert (job["status"], job["error_code"]) == ("failed", "not_found")
    assert lib["items"] == [] and lib["pending_jobs"][0]["id"] == job["id"]


async def test_expired_lease_is_reclaimed(client: AsyncClient, worker: IngestionWorker, migrated_database: str) -> None:
    project_id = await new_project(client)
    queued = await add(client, project_id, OA_PMID)
    # Simulate a worker that claimed the job and died.
    db(
        migrated_database,
        "UPDATE ingestion_jobs SET status='running', attempts=1, locked_until=now() - interval '1 minute'"
        " WHERE id = %s",
        (queued["job"]["id"],),
    )

    assert await drain(worker) == 1
    job = (await client.get(f"/api/v1/ingestion-jobs/{queued['job']['id']}")).json()
    assert (job["status"], job["attempts"]) == ("succeeded", 2)


async def test_jobs_wait_until_due(client: AsyncClient, worker: IngestionWorker, migrated_database: str) -> None:
    project_id = await new_project(client)
    queued = await add(client, project_id, OA_PMID)
    db(
        migrated_database,
        "UPDATE ingestion_jobs SET run_after = now() + interval '1 hour' WHERE id = %s",
        (queued["job"]["id"],),
    )

    assert await worker.run_once() is False


async def test_removing_from_project_keeps_shared_documents(
    client: AsyncClient, worker: IngestionWorker, migrated_database: str
) -> None:
    project_id = await new_project(client)
    await add(client, project_id, OA_PMID)
    await drain(worker)
    source_id = (await library(client, project_id))["items"][0]["source"]["id"]

    removed = await client.delete(f"/api/v1/projects/{project_id}/sources/{source_id}")
    again = await client.delete(f"/api/v1/projects/{project_id}/sources/{source_id}")

    assert (removed.status_code, again.status_code) == (204, 404)
    assert (await library(client, project_id))["items"] == []
    assert db(migrated_database, "SELECT count(*) FROM documents")[0][0] == 2


async def test_source_is_shared_between_projects_without_duplicate_documents(
    client: AsyncClient, worker: IngestionWorker, migrated_database: str
) -> None:
    a, b = await new_project(client), await new_project(client)

    await add(client, a, OA_PMID)
    await add(client, b, OA_PMID)
    await drain(worker)

    assert len((await library(client, a))["items"]) == len((await library(client, b))["items"]) == 1
    assert db(migrated_database, "SELECT kind, count(*) FROM documents GROUP BY kind ORDER BY kind") == [
        ("abstract", 1),
        ("fulltext", 1),
    ]


@pytest.mark.parametrize(
    ("path", "body", "code"),
    [
        ("/api/v1/projects/00000000-0000-0000-0000-000000000000/sources", {"identifier": "1"}, 404),
        (None, {"identifier": "abc"}, 422),
        (None, {"identifier": "1", "source": "europepmc"}, 422),
    ],
    ids=["missing-project", "bad-pmid", "unsupported-source"],
)
async def test_add_source_validation(client: AsyncClient, path: str | None, body: dict[str, str], code: int) -> None:
    url = path or f"/api/v1/projects/{await new_project(client)}/sources"

    assert (await client.post(url, json=body)).status_code == code


async def test_deleting_project_removes_its_jobs_and_links_but_not_documents(
    client: AsyncClient, worker: IngestionWorker, migrated_database: str
) -> None:
    project_id = await new_project(client)
    await add(client, project_id, OA_PMID)
    await drain(worker)

    await client.delete(f"/api/v1/projects/{project_id}")

    assert db(
        migrated_database,
        "SELECT (SELECT count(*) FROM ingestion_jobs), (SELECT count(*) FROM project_sources),"
        " (SELECT count(*) FROM documents)",
    ) == [(0, 0, 2)]
