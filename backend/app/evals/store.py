"""Shared eval helpers: dataset loading, git commit, report persistence (file + `evaluations` table)."""

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.evals.metrics import bootstrap_ci
from app.models import Evaluation


def load_jsonl(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    """(header, rows, sha256). The header must declare `dataset`, `version` and `split` (dev | test)."""
    raw = path.read_bytes()
    lines = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    header, rows = lines[0], lines[1:]
    if header.get("split") not in ("dev", "test"):
        raise SystemExit(f"{path}: header must declare split 'dev' or 'test'")
    ids = [r["id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"{path}: duplicate ids")
    return header, rows, hashlib.sha256(raw).hexdigest()


def git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git from PATH, fixed arguments
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def summarize(values: list[float]) -> dict[str, float | int]:
    """Mean with a 95% percentile-bootstrap CI over items (n reported so small samples are visible)."""
    if not values:
        return {"mean": float("nan"), "ci95_low": float("nan"), "ci95_high": float("nan"), "n": 0}
    low, high = bootstrap_ci(values)
    return {
        "mean": round(sum(values) / len(values), 4),
        "ci95_low": round(low, 4),
        "ci95_high": round(high, 4),
        "n": len(values),
    }


def headline(results: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Per-configuration means of every summarized metric, for the evaluations list."""
    return {
        config: {
            name: value["mean"]
            for name, value in result["metrics"].items()
            if isinstance(value, dict) and "mean" in value
        }
        for config, result in results.items()
    }


def _write_report(out: Path, report: dict[str, Any]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


async def save(
    session: AsyncSession, kind: Literal["retrieval", "answers"], report: dict[str, Any], out: Path | None
) -> str:
    run = report["evaluation_run"]
    evaluation = Evaluation(
        kind=kind,
        dataset=run["dataset"],
        dataset_version=run["dataset_version"],
        dataset_sha256=run["dataset_sha256"],
        split=run["split"],
        git_commit=run["git_commit"],
        summary=headline(report["results"]),
        report=json.loads(json.dumps(report, default=str)),
    )
    session.add(evaluation)
    await session.commit()
    report["evaluation_run"]["evaluation_id"] = str(evaluation.id)
    if out:
        await asyncio.to_thread(_write_report, out, report)
    return str(evaluation.id)
