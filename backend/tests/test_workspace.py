import asyncio
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any

import pytest
from httpx import AsyncClient

MISSING = str(uuid.uuid4())


async def create_project(client: AsyncClient, title: str = "TP53 in glioblastoma", **extra: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/projects", json={"title": title, **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def create_conversation(client: AsyncClient, project_id: str, title: str = "Scoping") -> dict[str, Any]:
    response = await client.post(f"/api/v1/projects/{project_id}/conversations", json={"title": title})
    assert response.status_code == 201, response.text
    return response.json()


async def post_message(client: AsyncClient, conversation_id: str, content: str) -> dict[str, Any]:
    response = await client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"content": content})
    assert response.status_code == 201, response.text
    return response.json()


# --- projects ---------------------------------------------------------------------------------------------------


async def test_project_crud_round_trip(client: AsyncClient) -> None:
    created = await create_project(client, "  Resistance variants  ", description="  EGFR T790M  ")
    assert created["title"] == "Resistance variants"
    assert created["description"] == "EGFR T790M"
    assert created["conversation_count"] == 0

    fetched = (await client.get(f"/api/v1/projects/{created['id']}")).json()
    assert fetched == created

    patched = await client.patch(f"/api/v1/projects/{created['id']}", json={"title": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"
    assert patched.json()["description"] == "EGFR T790M", "omitted fields must stay unchanged"
    assert patched.json()["updated_at"] > created["updated_at"]

    cleared = await client.patch(f"/api/v1/projects/{created['id']}", json={"description": None})
    assert cleared.json()["description"] is None

    assert (await client.delete(f"/api/v1/projects/{created['id']}")).status_code == 204
    gone = await client.get(f"/api/v1/projects/{created['id']}")
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "not_found"


async def test_blank_description_is_stored_as_null(client: AsyncClient) -> None:
    created = await create_project(client, description="   ")

    assert created["description"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": "   "},
        {"title": "x" * 201},
        {"title": "ok", "description": "x" * 5001},
        {"title": "ok", "owner": "someone"},
    ],
    ids=["missing-title", "blank-title", "title-too-long", "description-too-long", "unknown-field"],
)
async def test_project_create_rejects_invalid_payload(client: AsyncClient, payload: dict[str, Any]) -> None:
    response = await client.post("/api/v1/projects", json=payload)

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]
    assert error["trace_id"] == response.headers["X-Request-ID"]


async def test_project_patch_rejects_null_title(client: AsyncClient) -> None:
    project = await create_project(client)

    response = await client.patch(f"/api/v1/projects/{project['id']}", json={"title": None})

    assert response.status_code == 422
    assert (await client.get(f"/api/v1/projects/{project['id']}")).json()["title"] == project["title"]


async def test_project_list_is_most_recently_updated_first_and_paginated(client: AsyncClient) -> None:
    first = await create_project(client, "first")
    second = await create_project(client, "second")
    third = await create_project(client, "third")
    await client.patch(f"/api/v1/projects/{first['id']}", json={"title": "first, edited"})

    page = (await client.get("/api/v1/projects", params={"limit": 2})).json()
    assert page["total"] == 3
    assert [p["id"] for p in page["items"]] == [first["id"], third["id"]]

    rest = (await client.get("/api/v1/projects", params={"limit": 2, "offset": 2})).json()
    assert [p["id"] for p in rest["items"]] == [second["id"]]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
async def test_project_list_rejects_bad_pagination(client: AsyncClient, params: dict[str, int]) -> None:
    assert (await client.get("/api/v1/projects", params=params)).status_code == 422


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
async def test_missing_project_is_404(client: AsyncClient, method: str) -> None:
    kwargs = {"json": {"title": "x"}} if method == "patch" else {}
    response = await client.request(method.upper(), f"/api/v1/projects/{MISSING}", **kwargs)

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Project not found"


async def test_malformed_id_is_422(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/projects/not-a-uuid")).status_code == 422


# --- conversations ----------------------------------------------------------------------------------------------


async def test_conversations_are_scoped_to_their_project(client: AsyncClient) -> None:
    a = await create_project(client, "A")
    b = await create_project(client, "B")
    in_a = await create_conversation(client, a["id"], "in A")
    await create_conversation(client, b["id"], "in B")

    listing = (await client.get(f"/api/v1/projects/{a['id']}/conversations")).json()

    assert listing["total"] == 1
    assert [c["id"] for c in listing["items"]] == [in_a["id"]]
    assert (await client.get(f"/api/v1/projects/{a['id']}")).json()["conversation_count"] == 1


async def test_conversation_rename_and_delete(client: AsyncClient) -> None:
    project = await create_project(client)
    conversation = await create_conversation(client, project["id"])
    await post_message(client, conversation["id"], "hello")

    renamed = await client.patch(f"/api/v1/conversations/{conversation['id']}", json={"title": "Renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed"
    assert renamed.json()["message_count"] == 1

    assert (await client.delete(f"/api/v1/conversations/{conversation['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/conversations/{conversation['id']}")).status_code == 404
    assert (await client.get(f"/api/v1/conversations/{conversation['id']}/messages")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{project['id']}")).json()["conversation_count"] == 0


async def test_conversation_in_missing_project_is_404(client: AsyncClient) -> None:
    create = await client.post(f"/api/v1/projects/{MISSING}/conversations", json={"title": "x"})
    listing = await client.get(f"/api/v1/projects/{MISSING}/conversations")

    assert create.status_code == 404
    assert listing.status_code == 404


async def test_deleting_project_cascades_to_conversations_and_messages(client: AsyncClient) -> None:
    project = await create_project(client)
    conversation = await create_conversation(client, project["id"])
    await post_message(client, conversation["id"], "will be deleted")

    await client.delete(f"/api/v1/projects/{project['id']}")

    assert (await client.get(f"/api/v1/conversations/{conversation['id']}")).status_code == 404
    assert (await client.get(f"/api/v1/conversations/{conversation['id']}/messages")).status_code == 404


# --- messages ---------------------------------------------------------------------------------------------------


async def test_messages_are_sequenced_and_paginated_by_cursor(client: AsyncClient) -> None:
    project = await create_project(client)
    conversation = await create_conversation(client, project["id"])
    sent = [await post_message(client, conversation["id"], f"message {i}") for i in range(1, 6)]

    assert [m["sequence"] for m in sent] == [1, 2, 3, 4, 5]
    assert all(m["role"] == "user" for m in sent)

    url = f"/api/v1/conversations/{conversation['id']}/messages"
    first = (await client.get(url, params={"limit": 2})).json()
    assert [m["content"] for m in first["items"]] == ["message 1", "message 2"]
    assert first["has_more"] is True

    tail = (await client.get(url, params={"after_sequence": 3, "limit": 2})).json()
    assert [m["sequence"] for m in tail["items"]] == [4, 5]
    assert tail["has_more"] is False

    detail = (await client.get(f"/api/v1/conversations/{conversation['id']}")).json()
    assert detail["message_count"] == 5
    assert detail["updated_at"] >= sent[-1]["created_at"]


async def test_concurrent_posts_get_unique_gap_free_sequences(client: AsyncClient) -> None:
    project = await create_project(client)
    conversation = await create_conversation(client, project["id"])

    results = await asyncio.gather(*(post_message(client, conversation["id"], f"m{i}") for i in range(10)))

    assert sorted(m["sequence"] for m in results) == list(range(1, 11))


async def test_message_content_is_preserved_verbatim(client: AsyncClient) -> None:
    project = await create_project(client)
    conversation = await create_conversation(client, project["id"])
    content = "  Line one\n\n```python\nprint('x')\n```\n  <script>alert(1)</script> — čeština ✓"

    sent = await post_message(client, conversation["id"], content)
    stored = (await client.get(f"/api/v1/conversations/{conversation['id']}/messages")).json()["items"][0]

    assert sent["content"] == content
    assert stored["content"] == content


@pytest.mark.parametrize(
    "payload",
    [
        {"content": ""},
        {"content": " \n\t "},
        {"content": "x" * 20001},
        {"content": "hi", "role": "assistant"},
        {"content": "hi", "role": "system"},
    ],
    ids=["empty", "blank", "too-long", "assistant-role", "system-role"],
)
async def test_message_create_rejects_invalid_payload(client: AsyncClient, payload: dict[str, Any]) -> None:
    project = await create_project(client)
    conversation = await create_conversation(client, project["id"])

    response = await client.post(f"/api/v1/conversations/{conversation['id']}/messages", json=payload)

    assert response.status_code == 422
    assert (await client.get(f"/api/v1/conversations/{conversation['id']}")).json()["message_count"] == 0


async def test_message_to_missing_conversation_is_404(client: AsyncClient) -> None:
    response = await client.post(f"/api/v1/conversations/{MISSING}/messages", json={"content": "hi"})

    assert response.status_code == 404
    assert response.json()["error"]["message"] == "Conversation not found"


# --- persistence ------------------------------------------------------------------------------------------------


async def test_workspace_survives_application_restart(
    start_app: Callable[[], AbstractAsyncContextManager[AsyncClient]],
) -> None:
    async with start_app() as first_process:
        project = await create_project(first_process, "Persistent")
        conversation = await create_conversation(first_process, project["id"])
        await post_message(first_process, conversation["id"], "remember me")

    # A brand-new app (new engine, empty pool, no shared in-memory state) sees the same data.
    async with start_app() as second_process:
        assert (await second_process.get(f"/api/v1/projects/{project['id']}")).json()["conversation_count"] == 1
        messages = (await second_process.get(f"/api/v1/conversations/{conversation['id']}/messages")).json()
        assert [m["content"] for m in messages["items"]] == ["remember me"]
        # The sequence counter is persisted too, so numbering continues rather than restarting at 1.
        assert (await post_message(second_process, conversation["id"], "again"))["sequence"] == 2


async def test_unknown_route_uses_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
