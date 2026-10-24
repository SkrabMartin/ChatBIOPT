"""Entity-resolution evaluation against the live providers (M08 acceptance: species/identifier correctness).

    python -m app.evals.entities --dataset ../evals/datasets/entities-test-v0.1.jsonl \
        --out ../evals/reports/entities-test-v0.1.json

Dataset: JSONL; first line {"dataset", "version", "split"}; then {"id", "category", "query", "taxon_id",
"kind"?, "expect": {"status", "gene_ids"? | "elsewhere"?}}. An item is correct when the status matches and:
resolved -> the resolved gene is the expected one; ambiguous -> every expected gene is a candidate;
taxon_mismatch -> no candidate, and the expected gene is reported as belonging to another organism;
not_found -> no candidate. Safety metrics: `wrong_organism_rate` (any candidate outside the requested organism)
and `false_resolution_rate` (a single gene chosen where none should be) must stay 0.
Provider data changes between releases; the report records the releases it ran against.
"""

import argparse
import asyncio
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import __version__
from app.core.config import get_settings
from app.core.errors import UpstreamError
from app.db.session import create_engine
from app.entities.resolver import RESOLVER_VERSION, Resolution
from app.entities.service import EntityProviders
from app.evals.metrics import percentile
from app.evals.store import git_commit, load_jsonl, save, summarize
from app.integrations.http import RateLimiter

METRICS = ("accuracy", "status_accuracy", "wrong_organism_rate", "false_resolution_rate")


def score(item: dict[str, Any], result: Resolution) -> dict[str, float]:
    expect = item["expect"]
    got = [c.gene.gene_id for c in result.candidates]
    status_ok = result.status == expect["status"]
    if expect["status"] == "resolved":
        correct = status_ok and got[:1] == expect["gene_ids"]
    elif expect["status"] == "ambiguous":
        correct = status_ok and set(expect["gene_ids"]) <= set(got)
    elif expect["status"] == "taxon_mismatch":
        elsewhere = {e.identifier for e in result.found_elsewhere}
        correct = status_ok and not got and set(expect["elsewhere"]) <= elsewhere
    else:
        correct = status_ok and not got
    return {
        "accuracy": float(correct),
        "status_accuracy": float(status_ok),
        "wrong_organism_rate": float(any(c.gene.taxon_id != item["taxon_id"] for c in result.candidates)),
        "false_resolution_rate": float(result.status == "resolved" and expect["status"] != "resolved"),
    }


async def evaluate(dataset: Path, out: Path | None) -> dict[str, Any]:
    settings = get_settings()
    header, items, dataset_sha = load_jsonl(dataset)
    per_item: list[dict[str, Any]] = []
    values: dict[str, list[float]] = defaultdict(list)
    by_category: dict[str, list[float]] = defaultdict(list)
    latencies: list[float] = []
    providers: dict[str, str | None] = {}
    user_agent = f"BioResearchAI/{__version__} entity-eval ({settings.ncbi_email or 'no contact configured'})"
    async with httpx.AsyncClient(timeout=settings.ncbi_timeout_s, headers={"User-Agent": user_agent}) as http:
        resolver = EntityProviders.from_settings(
            http, settings, RateLimiter(settings.ncbi_requests_per_second)
        ).resolver
        for item in items:
            start = time.perf_counter()
            try:
                result = await resolver.resolve(item["query"], item["taxon_id"], item.get("kind"))
            except UpstreamError as exc:
                # A provider outage is not a resolver answer: the item is reported and excluded from the metrics.
                per_item.append({"id": item["id"], "error": exc.code, "message": exc.message})
                continue
            latencies.append((time.perf_counter() - start) * 1000)
            scores = score(item, result)
            for name, value in scores.items():
                values[name].append(value)
            by_category[item["category"]].append(scores["accuracy"])
            providers |= {p.provider: p.release for p in result.providers}
            per_item.append(
                {
                    "id": item["id"],
                    "category": item["category"],
                    "query": item["query"],
                    "taxon_id": item["taxon_id"],
                    "expect": item["expect"],
                    "status": result.status,
                    "candidates": [[c.gene.gene_id, c.gene.symbol, c.gene.taxon_id] for c in result.candidates],
                    "found_elsewhere": [[e.identifier, e.label, e.taxon_id] for e in result.found_elsewhere],
                    "warnings": result.warnings,
                    "latency_ms": round(latencies[-1]),
                    **scores,
                }
            )
    failed = [i for i in per_item if "error" in i]
    wrong = [i["id"] for i in per_item if i.get("accuracy") == 0.0]
    report: dict[str, Any] = {
        "evaluation_run": {
            "dataset": header["dataset"],
            "dataset_version": header["version"],
            "dataset_sha256": dataset_sha,
            "split": header["split"],
            "git_commit": git_commit(),
            "items": len(items),
            "resolver_version": RESOLVER_VERSION,
            "providers": providers,
            "created_at": datetime.now(UTC).isoformat(),
        },
        "results": {
            RESOLVER_VERSION: {
                "metrics": {name: summarize(values[name]) for name in METRICS},
                "latency_ms": {"p50": round(percentile(latencies, 50)), "p95": round(percentile(latencies, 95))}
                if latencies
                else None,
                "failed_runs": [{"id": i["id"], "error_code": i["error"]} for i in failed],
                "warnings": [f"incorrect: {', '.join(wrong)}"] if wrong else [],
            }
        },
        "by_category": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_category.items())},
        "items": per_item,
    }
    engine = create_engine(settings)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        await save(session, "entities", report, out)
    await engine.dispose()
    return report


def print_table(report: dict[str, Any]) -> None:
    run = report["evaluation_run"]
    print(f"{run['dataset']} v{run['dataset_version']} ({run['split']}) · {run['items']} items · {run['providers']}")
    for name, result in report["results"].items():
        cells = [f"{m}={result['metrics'][m]['mean']:.3f}" for m in METRICS]
        print(f"{name}: " + "  ".join(cells), f"  p50={result['latency_ms']['p50']} ms" if result["latency_ms"] else "")
        for warning in result["warnings"]:
            print(f"  ! {warning}")
        for failure in result["failed_runs"]:
            print(f"  ! provider failure on {failure['id']}: {failure['error_code']}")
    print("by category:", report["by_category"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    print_table(asyncio.run(evaluate(args.dataset, args.out)))


if __name__ == "__main__":
    main()
