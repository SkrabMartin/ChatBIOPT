import asyncio
import json
from typing import Any

import psycopg
import pytest
from httpx import AsyncClient, Response
from sqlalchemy.engine import make_url

from app.ai.prompts.research_assistant import PROMPT_VERSION, SYSTEM_PROMPT
from tests.conftest import StartApp
from tests.fakes import OLLAMA_DIGEST, FakeOllama

SseEvent = tuple[int | None, str, dict[str, Any]]


def parse_sse(response: Response) -> list[SseEvent]:
    assert response.headers["content-type"].startswith("text/event-stream")
    events: list[SseEvent] = []
    for block in response.text.split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":"))
        if fields:
            events.append((int(fields["id"]) if "id" in fields else None, fields["event"], json.loads(fields["data"])))
    return events


async def conversation_with_question(client: AsyncClient, question: str = "What does TP53 encode?") -> str:
    project = (await client.post("/api/v1/projects", json={"title": "P"})).json()
    conversation = (await client.post(f"/api/v1/projects/{project['id']}/conversations", json={"title": "C"})).json()
    await client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={"content": question})
    return str(conversation["id"])


async def start_run(client: AsyncClient, conversation_id: str, **body: Any) -> dict[str, Any]:
    response = await client.post(f"/api/v1/chat/{conversation_id}/runs", json=body)
    assert response.status_code == 202, response.text
    return dict(response.json())


async def events(client: AsyncClient, run_id: str, **headers: str) -> list[SseEvent]:
    return parse_sse(await client.get(f"/api/v1/runs/{run_id}/events", headers=headers))


async def messages(client: AsyncClient, conversation_id: str) -> list[dict[str, Any]]:
    return list((await client.get(f"/api/v1/conversations/{conversation_id}/messages")).json()["items"])


def db_execute(database_url: str, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(dsn, autocommit=True) as conn:
        cursor = conn.execute(sql, params)  # type: ignore[arg-type]
        return cursor.fetchall() if cursor.description else []


async def test_run_streams_and_persists_a_traceable_answer(client: AsyncClient, ollama: FakeOllama) -> None:
    conversation_id = await conversation_with_question(client)

    run = await start_run(client, conversation_id)
    assert run["status"] == "queued"
    assert (run["model_profile"], run["model_id"], run["prompt_version"]) == (
        "local-small",
        "qwen2.5:1.5b",
        PROMPT_VERSION,
    )

    stream = await events(client, run["id"])
    assert [e[1] for e in stream] == ["status", "delta", "delta", "delta", "completed"]
    assert [e[0] for e in stream] == [1, 2, 3, 4, 5]
    assert "".join(e[2]["text"] for e in stream if e[1] == "delta") == "Hello world."
    completed = stream[-1][2]
    assert completed["message"]["content"] == "Hello world."
    assert completed["message"]["role"] == "assistant"
    assert completed["message"]["run_id"] == run["id"]

    final = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert final["status"] == "succeeded"
    assert final["model_revision"] == f"sha256:{OLLAMA_DIGEST}"
    assert (final["input_tokens"], final["output_tokens"], final["finish_reason"]) == (42, 3, "stop")
    assert final["estimated_cost_usd"] == 0
    assert final["latency_ms"] >= final["time_to_first_token_ms"] >= 0
    assert final["context_message_count"] == 1
    assert len(final["config_hash"]) == 64

    thread = await messages(client, conversation_id)
    assert [(m["sequence"], m["role"], m["run_id"]) for m in thread] == [(1, "user", None), (2, "assistant", run["id"])]

    payload = ollama.chat_payloads[0]
    assert payload["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "What does TP53 encode?"},
    ]
    assert payload["options"] == {"temperature": 0.2, "num_predict": 1024, "num_ctx": 8192}


async def test_follow_up_run_sends_prior_turns_as_context(client: AsyncClient, ollama: FakeOllama) -> None:
    conversation_id = await conversation_with_question(client, "First question")
    await events(client, (await start_run(client, conversation_id))["id"])
    await client.post(f"/api/v1/conversations/{conversation_id}/messages", json={"content": "Follow-up"})

    await events(client, (await start_run(client, conversation_id))["id"])

    sent = ollama.chat_payloads[1]["messages"]
    assert [(m["role"], m["content"]) for m in sent[1:]] == [  # type: ignore[index]
        ("user", "First question"),
        ("assistant", "Hello world."),
        ("user", "Follow-up"),
    ]


@pytest.mark.parametrize(
    ("setup", "code", "detail"),
    [
        (lambda o: o.pulled.clear(), "model_unavailable", "ollama pull qwen2.5:1.5b"),
        (lambda o: setattr(o, "reachable", False), "model_unavailable", "not reachable"),
        (lambda o: setattr(o, "http_status", 500), "model_error", "HTTP 500"),
        (lambda o: setattr(o, "chunks", []), "model_error", "empty response"),
        (
            lambda o: setattr(
                o, "raw_lines", ['{"message":{"content":"partial"},"done":false}', '{"error":"out of memory"}']
            ),
            "model_error",
            "out of memory",
        ),
        (lambda o: setattr(o, "raw_lines", ['{"message":{"content":"cut"},"done":false}']), "model_error", "closed"),
        (lambda o: setattr(o, "raw_lines", ["not json"]), "model_error", "malformed"),
    ],
    ids=["not-pulled", "unreachable", "http-error", "empty", "error-mid-stream", "truncated", "malformed"],
)
async def test_model_failures_end_in_failed_runs_and_never_store_an_answer(
    client: AsyncClient, ollama: FakeOllama, setup: Any, code: str, detail: str
) -> None:
    setup(ollama)
    conversation_id = await conversation_with_question(client)

    run = await start_run(client, conversation_id)
    stream = await events(client, run["id"])

    kind, data = stream[-1][1], stream[-1][2]
    assert kind == "failed"
    assert data["error_code"] == code
    assert detail in data["message"]
    assert data["run"]["status"] == "failed"
    assert [m["role"] for m in await messages(client, conversation_id)] == ["user"]


async def test_cancel_stops_generation_without_storing_partial_text(client: AsyncClient, ollama: FakeOllama) -> None:
    ollama.hold = asyncio.Event()
    conversation_id = await conversation_with_question(client)
    run = await start_run(client, conversation_id)
    await asyncio.wait_for(ollama.first_chunk_sent.wait(), 5)

    cancel = await client.post(f"/api/v1/runs/{run['id']}/cancel")
    stream = await events(client, run["id"])

    assert cancel.status_code == 202
    assert [e[1] for e in stream][-1] == "cancelled"
    assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "cancelled"
    assert [m["role"] for m in await messages(client, conversation_id)] == ["user"]
    again = await client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert again.status_code == 409


async def test_only_one_active_run_per_conversation(client: AsyncClient, ollama: FakeOllama) -> None:
    ollama.hold = asyncio.Event()
    conversation_id = await conversation_with_question(client)
    first = await start_run(client, conversation_id)

    second = await client.post(f"/api/v1/chat/{conversation_id}/runs", json={})

    assert second.status_code == 409
    assert second.json()["error"]["message"] == "A response is already being generated for this conversation"
    ollama.hold.set()
    assert (await events(client, first["id"]))[-1][1] == "completed"


async def test_run_requires_an_unanswered_user_message(client: AsyncClient) -> None:
    project = (await client.post("/api/v1/projects", json={"title": "P"})).json()
    conversation = (await client.post(f"/api/v1/projects/{project['id']}/conversations", json={"title": "C"})).json()

    empty = await client.post(f"/api/v1/chat/{conversation['id']}/runs", json={})
    await client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={"content": "Q"})
    await events(client, (await start_run(client, conversation["id"]))["id"])
    answered = await client.post(f"/api/v1/chat/{conversation['id']}/runs", json={})

    assert empty.status_code == answered.status_code == 409


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"model_profile": "nope"}, 422, "unknown_model_profile"),
        ({"model_profile": "local-small", "temperature": 2}, 422, "validation_error"),
    ],
)
async def test_run_request_validation(client: AsyncClient, body: dict[str, Any], status: int, code: str) -> None:
    conversation_id = await conversation_with_question(client)

    response = await client.post(f"/api/v1/chat/{conversation_id}/runs", json=body)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


async def test_run_for_missing_conversation_is_404(client: AsyncClient) -> None:
    response = await client.post("/api/v1/chat/00000000-0000-0000-0000-000000000000/runs", json={})

    assert response.status_code == 404


async def test_run_exceeding_time_budget_fails_with_timeout(start_app: StartApp, ollama: FakeOllama) -> None:
    ollama.hold = asyncio.Event()
    async with start_app(run_max_duration_s=0.3) as client:
        conversation_id = await conversation_with_question(client)
        run = await start_run(client, conversation_id)
        stream = await events(client, run["id"])

    assert stream[-1][1] == "failed"
    assert stream[-1][2]["error_code"] == "run_timeout"


async def test_events_resume_after_last_event_id(client: AsyncClient) -> None:
    conversation_id = await conversation_with_question(client)
    run = await start_run(client, conversation_id)
    await events(client, run["id"])

    resumed = await events(client, run["id"], **{"Last-Event-ID": "3"})

    assert [e[0] for e in resumed] == [4, 5]


async def test_finished_run_outcome_is_replayed_after_restart(start_app: StartApp) -> None:
    async with start_app() as first:
        conversation_id = await conversation_with_question(first)
        run = await start_run(first, conversation_id)
        await events(first, run["id"])

    async with start_app() as second:
        replay = await events(second, run["id"])

    assert [e[1] for e in replay] == ["completed"]
    assert replay[0][2]["message"]["content"] == "Hello world."


async def test_runs_left_active_by_a_crash_are_marked_interrupted(start_app: StartApp, migrated_database: str) -> None:
    async with start_app() as first:
        conversation_id = await conversation_with_question(first)
    db_execute(
        migrated_database,
        "INSERT INTO runs (id, conversation_id, kind, status, model_profile, provider, model_id, prompt_version,"
        " config_hash, context_until_sequence) VALUES (gen_random_uuid(), %s, 'chat', 'running', 'local-small',"
        " 'ollama', 'qwen2.5:1.5b', 'research-assistant-v1', 'x', 1)",
        (conversation_id,),
    )

    async with start_app() as second:
        runs = (await second.get(f"/api/v1/conversations/{conversation_id}/runs")).json()
        stream = await events(second, runs[0]["id"])
        retry = await second.post(f"/api/v1/chat/{conversation_id}/runs", json={})

    assert runs[0]["status"] == "failed"
    assert runs[0]["error_code"] == "interrupted"
    assert stream[-1][2]["error_code"] == "interrupted"
    assert retry.status_code == 202, "the interrupted run must not block a retry"


async def test_deleting_conversation_removes_its_runs(client: AsyncClient, migrated_database: str) -> None:
    conversation_id = await conversation_with_question(client)
    await events(client, (await start_run(client, conversation_id))["id"])

    await client.delete(f"/api/v1/conversations/{conversation_id}")

    assert db_execute(migrated_database, "SELECT count(*) FROM runs") == [(0,)]


async def test_models_report_policy_and_availability(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/models")).json()

    by_id = {m["id"]: m for m in body["items"]}
    assert body["default_profile"] == "local-small"
    assert body["allow_external_models"] is False
    assert by_id["local-small"]["available"] is True
    assert by_id["local-small"]["revision"] == f"sha256:{OLLAMA_DIGEST}"
    assert by_id["local-general"]["available"] is False
    assert "ollama pull qwen2.5:7b" in by_id["local-general"]["unavailable_reason"]
    assert by_id["vllm-general"]["available"] is False
    assert all(m["blocked_reason"] is None for m in body["items"])
    reranks = {r["id"]: r for r in body["rerank_profiles"]}
    assert reranks["llm-listwise-general"]["generation_profile"] == "local-general"
    assert not any(r["is_default"] for r in reranks.values()), "reranking is off by default"
