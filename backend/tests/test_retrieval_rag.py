"""Retrieval and grounded answers end to end: ingest (worker) -> retrieve -> grounded run -> evidence board."""

import csv
import io
import json
from typing import Any

from httpx import AsyncClient

from app.ai.prompts.research_assistant import GROUNDED_PROMPT_VERSION, GROUNDED_SYSTEM_PROMPT
from app.workers.ingestion import IngestionWorker
from tests.fakes import OA_PMID, FakeOllama
from tests.test_runs_api import events, messages, start_run


async def project_with_article(client: AsyncClient, worker: IngestionWorker) -> str:
    project_id = str((await client.post("/api/v1/projects", json={"title": "RAG"})).json()["id"])
    await client.post(f"/api/v1/projects/{project_id}/sources", json={"identifier": OA_PMID})
    while await worker.run_once():
        pass
    return project_id


async def search(client: AsyncClient, project_id: str, query: str, mode: str, top_k: int = 5) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/retrieval/query", json={"project_id": project_id, "query": query, "mode": mode, "top_k": top_k}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def ask(client: AsyncClient, project_id: str, question: str) -> str:
    conversation = (await client.post(f"/api/v1/projects/{project_id}/conversations", json={"title": "Q"})).json()
    await client.post(f"/api/v1/conversations/{conversation['id']}/messages", json={"content": question})
    return str(conversation["id"])


async def test_ingestion_embeds_every_chunk(client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama) -> None:
    project_id = await project_with_article(client, worker)

    item = (await client.get(f"/api/v1/projects/{project_id}/sources")).json()["items"][0]
    embed = {s["step"]: s for s in item["latest_job"]["steps"]}["embed"]
    assert embed["status"] == "done"
    assert embed["embedding_space"].startswith("nomic-embed@sha256:")
    assert sum(len(batch) for batch in ollama.embed_requests) == embed["chunks"] == sum(item["chunk_counts"].values())
    assert all(text.startswith("search_document: ") for batch in ollama.embed_requests for text in batch)


async def test_keyword_retrieval_finds_exact_variant_identifiers(client: AsyncClient, worker: IngestionWorker) -> None:
    project_id = await project_with_article(client, worker)

    result = await search(client, project_id, "R110C", "keyword")

    assert result["mode"] == "keyword" and result["embedding_space"] is None
    assert result["hits"] and all("R110C" in h["text"] for h in result["hits"])
    assert all(h["keyword_rank"] is not None and h["semantic_rank"] is None for h in result["hits"])
    assert result["hits"][0]["document_kind"] == "fulltext", "full text supersedes the abstract document"


async def test_hybrid_fuses_both_retrievers(client: AsyncClient, worker: IngestionWorker) -> None:
    project_id = await project_with_article(client, worker)

    result = await search(client, project_id, "temozolomide resistance gliosarcoma", "hybrid", top_k=10)

    assert result["retrieval_version"] == "hybrid-rrf-v1"
    assert result["embedding_space"].startswith("nomic-embed@")
    assert any(h["keyword_rank"] and h["semantic_rank"] for h in result["hits"])
    scores = [h["fused_score"] for h in result["hits"]]
    assert scores == sorted(scores, reverse=True)


async def test_retrieval_never_crosses_projects(client: AsyncClient, worker: IngestionWorker) -> None:
    await project_with_article(client, worker)
    other = str((await client.post("/api/v1/projects", json={"title": "Empty"})).json()["id"])

    for mode in ("keyword", "semantic", "hybrid"):
        assert (await search(client, other, "TP53 R175H gliosarcoma", mode))["hits"] == []


async def test_hybrid_degrades_to_keyword_with_a_visible_warning(
    client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama
) -> None:
    project_id = await project_with_article(client, worker)
    ollama.pulled.discard("nomic-embed-text")

    result = await search(client, project_id, "R175H", "hybrid")

    assert result["mode"] == "keyword"
    assert result["warnings"] and "Semantic retrieval unavailable" in result["warnings"][0]
    assert result["hits"]


async def test_grounded_answer_keeps_only_valid_citations_and_records_claims(
    client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama
) -> None:
    project_id = await project_with_article(client, worker)
    conversation_id = await ask(client, project_id, "Which TP53 mutations (R175H, R110C) were found in SGS1?")
    ollama.chunks = [
        "STATUS: supported\n",
        "SGS1 carried distinct R175H and R110C subclones [E1]. ",
        "Both mutations were C:G>T:A transitions typical of temozolomide [E1, E2]. ",
        "They will respond to a new drug [E42].",
    ]

    run = await start_run(client, conversation_id)
    stream = await events(client, run["id"])

    assert [e[1] for e in stream][:3] == ["status", "evidence", "status"]
    evidence_event = stream[1][2]["items"]
    assert evidence_event and evidence_event[0]["label"] == "E1"
    final = stream[-1][2]
    assert stream[-1][1] == "completed"
    assert final["run"]["grounding"] == "project"
    assert final["run"]["prompt_version"] == GROUNDED_PROMPT_VERSION
    assert final["run"]["answer_class"] == "partially_supported"
    assert final["run"]["citation_stats"]["invalid_markers"] == 1
    assert "[E42]" not in final["message"]["content"] and "STATUS" not in final["message"]["content"]

    sent = ollama.chat_payloads[-1]["messages"]
    assert sent[0] == {"role": "system", "content": GROUNDED_SYSTEM_PROMPT}  # type: ignore[index]
    assert "<evidence>" in sent[-1]["content"] and "[E1]" in sent[-1]["content"]  # type: ignore[index]

    evidence = (await client.get(f"/api/v1/runs/{run['id']}/evidence")).json()
    cited = {e["label"]: e["cited"] for e in evidence}
    assert cited["E1"] is True and cited["E2"] is True
    assert all(not v for k, v in cited.items() if k not in ("E1", "E2"))

    board = (await client.get(f"/api/v1/projects/{project_id}/claims")).json()
    assert [(c["support_state"], [e["label"] for e in c["evidence"]]) for c in board] == [
        ("cited", ["E1"]),
        ("cited", ["E1", "E2"]),
        ("uncited", []),
    ]
    assert all(c["review_status"] == "requires_review" for c in board)
    assert board[0]["question"].startswith("Which TP53 mutations")


async def test_claim_review_and_csv_export(client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama) -> None:
    project_id = await project_with_article(client, worker)
    conversation_id = await ask(client, project_id, "What R175H findings are reported?")
    ollama.chunks = ["STATUS: supported\n", "R175H subclones expanded during treatment [E1]."]
    await events(client, (await start_run(client, conversation_id))["id"])
    claim = (await client.get(f"/api/v1/projects/{project_id}/claims")).json()[0]

    reviewed = await client.patch(
        f"/api/v1/claims/{claim['id']}", json={"review_status": "verified", "review_note": "Matches Fig. 2B"}
    )
    invalid = await client.patch(f"/api/v1/claims/{claim['id']}", json={"review_status": "true"})
    verified = (await client.get(f"/api/v1/projects/{project_id}/claims", params={"review_status": "verified"})).json()
    exported = await client.get(f"/api/v1/projects/{project_id}/claims", params={"format": "csv"})

    assert reviewed.status_code == 200 and reviewed.json()["reviewed_at"] is not None
    assert invalid.status_code == 422
    assert [c["id"] for c in verified] == [claim["id"]]
    assert exported.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert rows[0]["review_status"] == "verified" and rows[0]["pmid"] == OA_PMID and rows[0]["label"] == "E1"


async def test_question_without_matching_evidence_abstains_without_calling_the_model(
    client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama
) -> None:
    project_id = await project_with_article(client, worker)
    conversation_id = await ask(client, project_id, "Quantum chromodynamics of gluon plasma?")

    run = await start_run(client, conversation_id)
    stream = await events(client, run["id"])

    final = stream[-1][2]
    assert final["run"]["answer_class"] == "insufficient_evidence"
    assert final["message"]["content"].startswith("**Insufficient evidence.**")
    assert ollama.chat_payloads == [], "no model call when there is nothing to ground the answer in"
    assert (await client.get(f"/api/v1/runs/{run['id']}/evidence")).json() == []


async def test_retrieval_failure_is_a_tool_error_not_no_evidence(
    client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama
) -> None:
    project_id = await project_with_article(client, worker)
    conversation_id = await ask(client, project_id, "What about R175H?")
    ollama.embed_status = 500  # model listed as available, but embedding the query fails

    run = await start_run(client, conversation_id)
    stream = await events(client, run["id"])

    assert stream[-1][1] == "failed"
    assert stream[-1][2]["error_code"] == "tool_error"
    assert stream[-1][2]["run"]["answer_class"] == "tool_error"
    assert [m["role"] for m in await messages(client, conversation_id)] == ["user"]


async def test_grounding_can_be_disabled_and_auto_skips_empty_libraries(
    client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama
) -> None:
    project_id = await project_with_article(client, worker)
    with_library = await ask(client, project_id, "What about R175H?")
    empty_project = str((await client.post("/api/v1/projects", json={"title": "Empty"})).json()["id"])
    without_library = await ask(client, empty_project, "What about R175H?")

    explicit_none = await start_run(client, with_library, grounding="none")
    auto_empty = await start_run(client, without_library)
    await events(client, explicit_none["id"])
    await events(client, auto_empty["id"])

    assert explicit_none["grounding"] == auto_empty["grounding"] == "none"
    assert all(json.dumps(p).count("<evidence>") == 0 for p in ollama.chat_payloads)


async def test_retrieval_query_validation(client: AsyncClient) -> None:
    missing = await client.post(
        "/api/v1/retrieval/query", json={"project_id": "00000000-0000-0000-0000-000000000000", "query": "x"}
    )
    bad_mode = await client.post(
        "/api/v1/retrieval/query",
        json={"project_id": "00000000-0000-0000-0000-000000000000", "query": "x", "mode": "magic"},
    )

    assert (missing.status_code, bad_mode.status_code) == (404, 422)


async def test_single_common_word_match_is_not_evidence(
    client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama
) -> None:
    project_id = await project_with_article(client, worker)
    # Only "role" occurs in the corpus; the rest of the question is unrelated.
    conversation_id = await ask(client, project_id, "What role does quantum chromodynamics play in gluon plasma?")

    hits = (await search(client, project_id, "What role does quantum chromodynamics play in gluon plasma?", "keyword"))[
        "hits"
    ]
    stream = await events(client, (await start_run(client, conversation_id))["id"])

    assert hits and all(h["keyword_coverage"] < 0.5 and not h["counts_as_evidence"] for h in hits)
    assert stream[-1][2]["run"]["answer_class"] == "insufficient_evidence"
    assert ollama.chat_payloads == []


async def test_model_abstention_does_not_create_claims(
    client: AsyncClient, worker: IngestionWorker, ollama: FakeOllama
) -> None:
    project_id = await project_with_article(client, worker)
    conversation_id = await ask(client, project_id, "Was R175H tested against drug Z?")
    ollama.chunks = ["STATUS: insufficient_evidence\n", "The passages do not mention drug Z or its effect on R175H."]

    final = (await events(client, (await start_run(client, conversation_id))["id"]))[-1][2]

    assert final["run"]["answer_class"] == "insufficient_evidence"
    assert (await client.get(f"/api/v1/projects/{project_id}/claims")).json() == []
