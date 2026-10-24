"""Offline retrieval evaluation: keyword vs semantic vs hybrid on the same queries (blueprint §10.1).

    python -m app.evals.retrieval --project <uuid> --dataset ../evals/datasets/retrieval-dev-v0.1.jsonl \
        --out ../evals/reports/retrieval-dev-v0.1.json

The dataset is JSONL. The first line is a header {"dataset", "version", "corpus_pmids": [...]}; every other line is
{"id", "query", "kind", "relevant": [{"pmid", "contains", "grade"}]}. Relevance is declared by quoting text, not by
chunk id, so labels survive re-chunking; each phrase must match at least one chunk or the run aborts.
The project must contain exactly the corpus PMIDs, fully embedded in the current embedding space.
"""

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.gateway.router import ModelRouter
from app.ai.registry import load_registry
from app.core.config import get_settings
from app.db.session import create_engine
from app.evals.metrics import bootstrap_ci, ndcg_at_k, percentile, recall_at_k, reciprocal_rank
from app.ingestion.chunking import CHUNKER_VERSION
from app.retrieval.embeddings import EmbeddingSpace, resolve_space
from app.retrieval.search import RRF_K, Mode, retrieve

MODES: tuple[Mode, ...] = ("keyword", "semantic", "hybrid")
TOP_K = 10


@dataclass(frozen=True)
class Query:
    id: str
    text: str
    kind: str
    relevant: list[dict[str, Any]]


def load_dataset(path: Path) -> tuple[dict[str, Any], list[Query], str]:
    raw = path.read_bytes()
    lines = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    header, rows = lines[0], lines[1:]
    queries = [Query(r["id"], r["query"], r.get("kind", "general"), r["relevant"]) for r in rows]
    if len({q.id for q in queries}) != len(queries):
        raise SystemExit("duplicate query ids in dataset")
    return header, queries, hashlib.sha256(raw).hexdigest()


_CORPUS = text(
    """
WITH latest AS (
    SELECT DISTINCT ON (d.source_id, d.kind) d.id, d.source_id, d.kind
    FROM documents d JOIN project_sources ps ON ps.source_id = d.source_id AND ps.project_id = :project_id
    ORDER BY d.source_id, d.kind, d.version DESC
),
eligible AS (
    SELECT l.id, l.source_id FROM latest l
    WHERE l.kind = 'fulltext'
       OR NOT EXISTS (SELECT 1 FROM latest f WHERE f.source_id = l.source_id AND f.kind = 'fulltext')
)
SELECT c.id, c.text, s.external_id,
       EXISTS (SELECT 1 FROM chunk_embeddings e WHERE e.chunk_id = c.id AND e.embedding_space = :space) AS embedded
FROM chunks c JOIN eligible el ON el.id = c.document_id JOIN sources s ON s.id = el.source_id
WHERE c.chunker_version = :chunker_version
"""
)


async def resolve_relevance(
    session: AsyncSession, project_id: uuid.UUID, space: EmbeddingSpace, header: dict[str, Any], queries: list[Query]
) -> tuple[dict[str, dict[uuid.UUID, int]], int]:
    rows = (
        await session.execute(
            _CORPUS, {"project_id": project_id, "space": space.name, "chunker_version": CHUNKER_VERSION}
        )
    ).all()
    pmids = {row.external_id for row in rows}
    if pmids != set(header["corpus_pmids"]):
        raise SystemExit(f"project corpus {sorted(pmids)} != dataset corpus {sorted(header['corpus_pmids'])}")
    missing = sum(1 for row in rows if not row.embedded)
    if missing:
        raise SystemExit(f"{missing} chunks lack embeddings in {space.name}; refresh the sources first")
    relevance: dict[str, dict[uuid.UUID, int]] = {}
    for query in queries:
        grades: dict[uuid.UUID, int] = {}
        for item in query.relevant:
            phrase = item["contains"].lower()
            matches = [r.id for r in rows if r.external_id == item["pmid"] and phrase in r.text.lower()]
            if not matches:
                raise SystemExit(f"{query.id}: no chunk of PMID {item['pmid']} contains {item['contains']!r}")
            for chunk_id in matches:
                grades[chunk_id] = max(grades.get(chunk_id, 0), int(item["grade"]))
        relevance[query.id] = grades
    return relevance, len(rows)


def summarize(per_query: list[dict[str, float]], metric: str) -> dict[str, float]:
    values = [q[metric] for q in per_query]
    low, high = bootstrap_ci(values)
    return {"mean": round(sum(values) / len(values), 4), "ci95_low": round(low, 4), "ci95_high": round(high, 4)}


async def evaluate(project_id: uuid.UUID, dataset: Path) -> dict[str, Any]:
    settings = get_settings()
    header, queries, dataset_sha = load_dataset(dataset)
    engine = create_engine(settings)
    async with httpx.AsyncClient(timeout=settings.model_read_timeout_s) as http:
        models = ModelRouter(
            load_registry(settings.model_registry_path),
            http,
            ollama_base_url=settings.ollama_base_url,
            allow_external=settings.allow_external_models,
        )
        space = await resolve_space(models)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            relevance, corpus_chunks = await resolve_relevance(session, project_id, space, header, queries)
            results: dict[str, Any] = {}
            for mode in MODES:
                per_query: list[dict[str, Any]] = []
                for query in queries:
                    result = await retrieve(session, project_id, query.text, mode=mode, top_k=TOP_K, space=space)
                    ranked = [hit.chunk_id for hit in result.hits]
                    grades = relevance[query.id]
                    per_query.append(
                        {
                            "id": query.id,
                            "kind": query.kind,
                            "recall@5": recall_at_k(ranked, grades, 5),
                            "recall@10": recall_at_k(ranked, grades, 10),
                            "mrr@10": reciprocal_rank(ranked, grades, 10),
                            "ndcg@10": ndcg_at_k(ranked, grades, 10),
                            "latency_ms": result.latency_ms,
                        }
                    )
                latencies = [q["latency_ms"] for q in per_query]
                results[mode] = {
                    "metrics": {m: summarize(per_query, m) for m in ("recall@5", "recall@10", "mrr@10", "ndcg@10")},
                    "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95)},
                    "by_kind": {
                        kind: {
                            m: round(sum(q[m] for q in rows) / len(rows), 4) for m in ("recall@10", "mrr@10", "ndcg@10")
                        }
                        for kind in sorted({q["kind"] for q in per_query})
                        if (rows := [q for q in per_query if q["kind"] == kind])
                    },
                    "per_query": per_query,
                }
    await engine.dispose()

    report = {
        "evaluation_run": {
            "created_at": datetime.now(UTC).isoformat(),
            "git_commit": _git_commit(),
            "dataset": header["dataset"],
            "dataset_version": header["version"],
            "dataset_sha256": dataset_sha,
            "queries": len(queries),
            "corpus_pmids": header["corpus_pmids"],
            "corpus_chunks": corpus_chunks,
            "chunker_version": CHUNKER_VERSION,
            "embedding_space": space.name,
            "retrieval": {"top_k": TOP_K, "rrf_k": RRF_K, "fusion": "reciprocal rank fusion", "reranker": None},
            "ci": "percentile bootstrap of the mean over queries, 2000 resamples, seed 7",
        },
        "results": results,
    }
    return report


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git from PATH, fixed arguments
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def print_table(report: dict[str, Any]) -> None:
    run = report["evaluation_run"]
    print(f"{run['dataset']} v{run['dataset_version']} · {run['queries']} queries · {run['embedding_space']}")
    print(f"{'mode':<9} {'recall@5':>16} {'recall@10':>16} {'MRR@10':>16} {'nDCG@10':>16} {'p50 ms':>7} {'p95 ms':>7}")
    for mode, result in report["results"].items():
        cells = [
            f"{m['mean']:.3f} [{m['ci95_low']:.2f}-{m['ci95_high']:.2f}]"
            for m in (result["metrics"][k] for k in ("recall@5", "recall@10", "mrr@10", "ndcg@10"))
        ]
        latency = result["latency_ms"]
        print(f"{mode:<9} " + " ".join(f"{c:>16}" for c in cells) + f" {latency['p50']:>7} {latency['p95']:>7}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", type=uuid.UUID, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.project, args.dataset))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print_table(report)
    sys.exit(0)


if __name__ == "__main__":
    main()
