"""M09 acceptance: cross-project security regression suite.

1. Route audit (static): every API route is classified below. A new route fails this suite until it is given a
   classification, and project-scoped routes must declare exactly the role listed here.
2. Behavioural matrices: one fully populated project (library source, document, chunk, ingestion job,
   conversation, grounded run, claim, pinned gene) is probed by an anonymous client, a client without the CSRF
   header, a non-member, a viewer and an editor, on every route.
"""

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi.dependencies.models import Dependant
from httpx import AsyncClient

from app.auth.deps import current_actor
from app.main import create_app
from app.workers.ingestion import IngestionWorker
from tests.conftest import Account, StartApp, create_user, make_settings
from tests.fakes import FakeOllama
from tests.test_retrieval_rag import ask, project_with_article
from tests.test_runs_api import events, start_run

PUBLIC = {("GET", "/api/v1/health"), ("POST", "/api/v1/auth/login")}
ADMIN = {("GET", "/api/v1/admin/users"), ("POST", "/api/v1/admin/users"), ("PATCH", "/api/v1/admin/users/{user_id}")}
# Signed-in users, no project scope: shared public reference data (PubMed, entity providers, evaluation reports
# on public datasets), the caller's own account, and project creation/listing (filtered to memberships).
GLOBAL = {
    ("GET", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/password"),
    ("GET", "/api/v1/projects"),
    ("POST", "/api/v1/projects"),
    ("POST", "/api/v1/literature/search"),
    ("GET", "/api/v1/literature/{source}/{identifier}"),
    ("GET", "/api/v1/models"),
    ("GET", "/api/v1/evaluations"),
    ("GET", "/api/v1/evaluations/{evaluation_id}"),
    ("GET", "/api/v1/entities/organisms"),
    ("POST", "/api/v1/entities/resolve"),
    ("GET", "/api/v1/entities/{entity_id}"),
}
# The project is named in the request body and authorized in the handler.
BODY_SCOPED = {("POST", "/api/v1/retrieval/query")}
# Shared library objects: readable when linked to any project of the caller.
LIBRARY = {
    ("GET", "/api/v1/sources/{source_id}/documents"): "source_id",
    ("GET", "/api/v1/documents/{document_id}"): "document_id",
    ("GET", "/api/v1/chunks/{chunk_id}"): "chunk_id",
}
# Project-scoped: minimum role, authorized through the path parameter's project.
SCOPED: dict[tuple[str, str], tuple[str, str]] = {
    ("GET", "/api/v1/projects/{project_id}"): ("project_id", "viewer"),
    ("PATCH", "/api/v1/projects/{project_id}"): ("project_id", "owner"),
    ("DELETE", "/api/v1/projects/{project_id}"): ("project_id", "owner"),
    ("GET", "/api/v1/projects/{project_id}/members"): ("project_id", "viewer"),
    ("POST", "/api/v1/projects/{project_id}/members"): ("project_id", "owner"),
    ("PATCH", "/api/v1/projects/{project_id}/members/{user_id}"): ("project_id", "owner"),
    # Any member may remove themself; removing someone else is checked for owner in the handler.
    ("DELETE", "/api/v1/projects/{project_id}/members/{user_id}"): ("project_id", "viewer"),
    ("GET", "/api/v1/projects/{project_id}/conversations"): ("project_id", "viewer"),
    ("POST", "/api/v1/projects/{project_id}/conversations"): ("project_id", "editor"),
    ("GET", "/api/v1/conversations/{conversation_id}"): ("conversation_id", "viewer"),
    ("PATCH", "/api/v1/conversations/{conversation_id}"): ("conversation_id", "editor"),
    ("DELETE", "/api/v1/conversations/{conversation_id}"): ("conversation_id", "editor"),
    ("GET", "/api/v1/conversations/{conversation_id}/messages"): ("conversation_id", "viewer"),
    ("POST", "/api/v1/conversations/{conversation_id}/messages"): ("conversation_id", "editor"),
    ("GET", "/api/v1/conversations/{conversation_id}/runs"): ("conversation_id", "viewer"),
    ("POST", "/api/v1/chat/{conversation_id}/runs"): ("conversation_id", "editor"),
    ("GET", "/api/v1/runs/{run_id}"): ("run_id", "viewer"),
    ("POST", "/api/v1/runs/{run_id}/cancel"): ("run_id", "editor"),
    ("GET", "/api/v1/runs/{run_id}/evidence"): ("run_id", "viewer"),
    ("GET", "/api/v1/runs/{run_id}/events"): ("run_id", "viewer"),
    ("GET", "/api/v1/projects/{project_id}/sources"): ("project_id", "viewer"),
    ("POST", "/api/v1/projects/{project_id}/sources"): ("project_id", "editor"),
    ("POST", "/api/v1/projects/{project_id}/sources/{source_id}/refresh"): ("project_id", "editor"),
    ("DELETE", "/api/v1/projects/{project_id}/sources/{source_id}"): ("project_id", "editor"),
    ("GET", "/api/v1/ingestion-jobs/{job_id}"): ("job_id", "viewer"),
    ("GET", "/api/v1/projects/{project_id}/claims"): ("project_id", "viewer"),
    ("PATCH", "/api/v1/claims/{claim_id}"): ("claim_id", "editor"),
    ("GET", "/api/v1/projects/{project_id}/entities"): ("project_id", "viewer"),
    ("POST", "/api/v1/projects/{project_id}/entities"): ("project_id", "editor"),
    ("DELETE", "/api/v1/projects/{project_id}/entities/{entity_id}"): ("project_id", "editor"),
}

PARAM = re.compile(r"{(\w+)}")


@dataclass(frozen=True)
class RouteInfo:
    path: str
    dependant: Dependant


def api_routes() -> list[tuple[str, str, RouteInfo]]:
    """Every (method, path) of the API with the dependencies that actually run for it (router-level included).

    FastAPI >= 0.16x nests included routers lazily; their effective routes carry the merged dependencies.
    """
    app = create_app(make_settings("postgresql+psycopg://unused@127.0.0.1:1/none"))
    found: list[tuple[str, str, RouteInfo]] = []
    for route in app.routes:
        contexts = route.effective_route_contexts() if hasattr(route, "effective_route_contexts") else []
        for context in contexts:
            if context.dependant is None or not context.path.startswith("/api/v1"):
                continue
            found += [(m, context.path, RouteInfo(context.path, context.dependant)) for m in sorted(context.methods)]
    assert len(found) > 40, "route discovery broke (FastAPI internals changed?)"
    return found


def dependency_calls(route: RouteInfo) -> list[Callable[..., Any]]:
    calls: list[Callable[..., Any]] = []
    pending = list(route.dependant.dependencies)
    while pending:
        dependant = pending.pop()
        if dependant.call is not None:
            calls.append(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


def fill(path: str, ids: dict[str, str]) -> str:
    return PARAM.sub(lambda m: ids[m.group(1)], path)


# --- 1. static route audit ---------------------------------------------------------------------------------------


def test_every_route_is_classified_and_declares_the_listed_authorization() -> None:
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for method, path, route in api_routes():
        key = (method, path)
        seen.add(key)
        calls = dependency_calls(route)
        authz = {c.__authz__ for c in calls if hasattr(c, "__authz__")}
        signed_in = current_actor in calls
        if key in PUBLIC:
            if signed_in:
                problems.append(f"{method} {path}: public route requires a session")
            continue
        if not signed_in:
            problems.append(f"{method} {path}: reachable without a session")
        if key in SCOPED:
            if authz != {SCOPED[key]}:
                problems.append(f"{method} {path}: declares {authz or 'no project check'}, expected {SCOPED[key]}")
        elif key in LIBRARY:
            if authz != {(LIBRARY[key], "viewer")}:
                problems.append(f"{method} {path}: declares {authz or 'no library check'}")
        elif key in GLOBAL | ADMIN | BODY_SCOPED:
            if authz:
                problems.append(f"{method} {path}: unexpected project check {authz}")
            if key in ADMIN and not any(c.__name__ == "require_admin" for c in calls):
                problems.append(f"{method} {path}: admin route without require_admin")
        else:
            problems.append(f"{method} {path}: unclassified route; add it to tests/test_security.py")
        scoped_params = {"project_id", "conversation_id", "run_id", "claim_id", "job_id"} & set(PARAM.findall(path))
        if scoped_params and key not in SCOPED:
            problems.append(f"{method} {path}: project-scoped parameter {scoped_params} without a role requirement")
    stale = (set(SCOPED) | set(LIBRARY) | GLOBAL | ADMIN | PUBLIC | BODY_SCOPED) - seen
    problems += [f"{m} {p}: classified but no longer exists" for m, p in sorted(stale)]
    assert problems == []


# --- 2. behavioural matrices -------------------------------------------------------------------------------------


async def build_world(
    client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama, owner: Account
) -> dict[str, str]:
    """A project with every kind of project-owned and library object, created through the API by `owner`."""
    project_id = await project_with_article(client, worker)
    item = (await client.get(f"/api/v1/projects/{project_id}/sources")).json()["items"][0]
    document_id = item["documents"][0]["id"]
    chunk_id = (await client.get(f"/api/v1/documents/{document_id}")).json()["chunks"][0]["id"]
    conversation_id = await ask(client, project_id, "What R175H findings are reported?")
    ollama.chunks = ["STATUS: supported\n", "R175H subclones expanded during treatment [E1]."]
    run = await start_run(client, conversation_id)
    await events(client, run["id"])
    claim_id = (await client.get(f"/api/v1/projects/{project_id}/claims")).json()[0]["id"]
    pinned = await client.post(f"/api/v1/projects/{project_id}/entities", json={"gene_id": "7157", "taxon_id": 9606})
    assert pinned.status_code == 201, pinned.text
    return {
        "project_id": project_id,
        "conversation_id": conversation_id,
        "run_id": run["id"],
        "claim_id": claim_id,
        "job_id": item["latest_job"]["id"],
        "source_id": item["source"]["id"],
        "document_id": document_id,
        "chunk_id": chunk_id,
        "entity_id": pinned.json()["entity"]["id"],
        "user_id": str(owner.id),
        "evaluation_id": str(uuid.uuid4()),
        "source": "pubmed",
        "identifier": "29416795",
    }


async def call(client: AsyncClient, method: str, path: str, **kwargs: Any) -> int:
    body = {} if method != "GET" else None
    return (await client.request(method, path, json=body, **kwargs)).status_code


async def test_anonymous_requests_are_rejected_on_every_non_public_route(start_app: StartApp) -> None:
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "project_id",
            "conversation_id",
            "run_id",
            "claim_id",
            "job_id",
            "source_id",
            "document_id",
            "chunk_id",
            "entity_id",
            "user_id",
            "evaluation_id",
        )
    }
    ids |= {"source": "pubmed", "identifier": "29416795"}
    async with start_app(as_user=None) as anonymous:
        problems = [
            f"{method} {path}: {status}"
            for method, path, _ in api_routes()
            if (method, path) not in PUBLIC and (status := await call(anonymous, method, fill(path, ids))) != 401
        ]
    assert problems == []


async def test_state_changes_without_the_csrf_header_are_rejected(client: AsyncClient) -> None:
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "project_id",
            "conversation_id",
            "run_id",
            "claim_id",
            "job_id",
            "source_id",
            "document_id",
            "chunk_id",
            "entity_id",
            "user_id",
        )
    }
    problems: list[str] = []
    for method, path, _ in api_routes():
        if method == "GET":
            continue
        response = await client.request(method, fill(path, ids), json={}, headers={"X-Requested-With": ""})
        if response.status_code != 403 or response.json()["error"]["code"] != "csrf_failed":
            problems.append(f"{method} {path}: {response.status_code}")
    assert problems == []
    # Reads stay possible without the header (links, SSE).
    assert (await client.get("/api/v1/projects", headers={"X-Requested-With": ""})).status_code == 200


async def test_outsider_cannot_see_or_touch_anything_in_another_users_project(
    client: AsyncClient,
    worker: IngestionWorker,
    ollama: FakeOllama,
    start_app: StartApp,
    user: Account,
    migrated_database: str,
) -> None:
    ids = await build_world(client, worker, ollama, user)
    bob = await create_user(migrated_database, "bob@example.org")
    async with start_app(as_user=bob) as outsider:
        problems = [
            f"{method} {path}: {status}"
            for method, path, _ in api_routes()
            if ((method, path) in SCOPED or (method, path) in LIBRARY)
            and (status := await call(outsider, method, fill(path, ids))) != 404
        ]
        # The body-scoped search cannot reach another project's passages or vectors either.
        search = await outsider.post(
            "/api/v1/retrieval/query", json={"project_id": ids["project_id"], "query": "R175H", "mode": "hybrid"}
        )
        listed = (await outsider.get("/api/v1/projects")).json()
        # Bob's own project with the same article does not open Alice's run, claims or job.
        own = (await outsider.post("/api/v1/projects", json={"title": "Bob"})).json()["id"]
        own_claims = (await outsider.get(f"/api/v1/projects/{own}/claims")).json()
    assert problems == []
    assert search.status_code == 404
    assert listed["total"] == 0 and listed["items"] == []
    assert own_claims == []
    # Alice's data is untouched by the attempts.
    assert (await client.get(f"/api/v1/projects/{ids['project_id']}")).json()["role"] == "owner"
    assert len((await client.get(f"/api/v1/projects/{ids['project_id']}/members")).json()) == 1


async def test_viewer_reads_everything_but_changes_nothing(
    client: AsyncClient,
    worker: IngestionWorker,
    ollama: FakeOllama,
    start_app: StartApp,
    user: Account,
    migrated_database: str,
) -> None:
    ids = await build_world(client, worker, ollama, user)
    carol = await create_user(migrated_database, "carol@example.org")
    added = await client.post(
        f"/api/v1/projects/{ids['project_id']}/members", json={"email": carol.email, "role": "viewer"}
    )
    assert added.status_code == 201
    problems: list[str] = []
    async with start_app(as_user=carol) as viewer:
        for method, path, _ in api_routes():
            key = (method, path)
            if key in LIBRARY or (key in SCOPED and SCOPED[key][1] == "viewer" and method == "GET"):
                expected = 200
            elif key in SCOPED:
                expected = 403  # includes removing another member (user_id is the owner's)
            else:
                continue
            if (status := await call(viewer, method, fill(path, ids))) != expected:
                problems.append(f"{method} {path}: {status}, expected {expected}")
        search = await viewer.post(
            "/api/v1/retrieval/query", json={"project_id": ids["project_id"], "query": "R175H", "mode": "keyword"}
        )
        role = (await viewer.get(f"/api/v1/projects/{ids['project_id']}")).json()["role"]
    assert problems == []
    assert search.status_code == 200 and search.json()["hits"]
    assert role == "viewer"


async def test_editor_works_in_the_project_but_cannot_administer_it(
    client: AsyncClient,
    worker: IngestionWorker,
    ollama: FakeOllama,
    start_app: StartApp,
    user: Account,
    migrated_database: str,
) -> None:
    ids = await build_world(client, worker, ollama, user)
    dave = await create_user(migrated_database, "dave@example.org")
    await client.post(f"/api/v1/projects/{ids['project_id']}/members", json={"email": dave.email, "role": "editor"})
    async with start_app(as_user=dave) as editor:
        problems = [
            f"{method} {path}: {status}"
            for (method, path), (_, need) in SCOPED.items()
            if need == "owner" and (status := await call(editor, method, fill(path, ids))) != 403
        ]
        message = await editor.post(
            f"/api/v1/conversations/{ids['conversation_id']}/messages", json={"content": "Editor follow-up"}
        )
        review = await editor.patch(f"/api/v1/claims/{ids['claim_id']}", json={"review_status": "disputed"})
    assert problems == []
    assert message.status_code == 201 and message.json()["author_name"] == "Dave"
    assert review.status_code == 200


async def test_removed_member_loses_access_immediately(
    client: AsyncClient, start_app: StartApp, migrated_database: str
) -> None:
    project_id = (await client.post("/api/v1/projects", json={"title": "Shared"})).json()["id"]
    erin = await create_user(migrated_database, "erin@example.org")
    await client.post(f"/api/v1/projects/{project_id}/members", json={"email": erin.email, "role": "editor"})
    async with start_app(as_user=erin) as member:
        assert (await member.get(f"/api/v1/projects/{project_id}")).status_code == 200
        removed = await client.delete(f"/api/v1/projects/{project_id}/members/{erin.id}")
        after = await member.get(f"/api/v1/projects/{project_id}")
    assert removed.status_code == 204
    assert after.status_code == 404
