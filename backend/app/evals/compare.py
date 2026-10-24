"""Compare a baseline and a candidate (blueprint §10.4: report improvements and regressions).

    # two configurations within one report (e.g. hybrid vs hybrid + reranker)
    python -m app.evals.compare ../evals/reports/retrieval-test-v0.1.json --baseline hybrid \
        --candidate hybrid+rerank:llm-listwise-small
    # the same configuration across two reports (e.g. before and after a change)
    python -m app.evals.compare old.json new.json --baseline hybrid --candidate hybrid --fail-on-regression

For per-item metrics the difference is tested with a paired bootstrap over the items both sides share (95% CI of
the mean difference); "improved"/"regressed" means the CI excludes zero. Aggregate-only metrics (no per-item values)
are shown without a significance call.
"""

import argparse
import json
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# metric -> (items key, filter, value) for per-item paired testing
PerItem = tuple[str, Callable[[dict[str, Any]], bool], Callable[[dict[str, Any]], float | None]]


def _field(name: str) -> Callable[[dict[str, Any]], float | None]:
    return lambda item: None if item.get(name) is None else float(item[name])


def _ok(item: dict[str, Any]) -> bool:
    return bool(item.get("status", "succeeded") == "succeeded")


PER_ITEM: dict[str, PerItem] = {
    **{m: ("per_query", lambda _i: True, _field(m)) for m in ("recall@5", "recall@10", "mrr@10", "ndcg@10")},
    "answer_accuracy": ("per_item", lambda i: _ok(i) and i["type"] == "answerable", _field("score")),
    "false_abstention_rate": ("per_item", lambda i: _ok(i) and i["type"] == "answerable", _field("abstained")),
    "abstention_accuracy": ("per_item", lambda i: _ok(i) and i["type"] == "unanswerable", _field("abstained")),
    "reference_cited_rate": ("per_item", lambda i: _ok(i) and i["type"] == "answerable", _field("reference_cited")),
}
LOWER_IS_BETTER = frozenset({"false_abstention_rate", "uncited_claim_rate"})


@dataclass(frozen=True)
class Row:
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    ci: tuple[float, float] | None
    n: int
    verdict: str


def _mean(value: Any) -> float | None:
    if isinstance(value, dict):
        return float(value["mean"]) if value.get("n", 1) else None
    return None if value is None else float(value)


def paired_bootstrap(deltas: list[float], iterations: int = 2000, seed: int = 11) -> tuple[float, float]:
    rng = random.Random(seed)  # noqa: S311 - statistical resampling
    n = len(deltas)
    means = sorted(sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    return means[int(0.025 * iterations)], means[min(iterations - 1, int(0.975 * iterations))]


def compare(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[Row]:
    rows: list[Row] = []
    metrics = [m for m in baseline["metrics"] if m in candidate["metrics"]]
    for metric in metrics:
        base_mean, cand_mean = _mean(baseline["metrics"][metric]), _mean(candidate["metrics"][metric])
        delta = None if base_mean is None or cand_mean is None else round(cand_mean - base_mean, 4)
        ci: tuple[float, float] | None = None
        n = 0
        verdict = "aggregate only"
        if metric in PER_ITEM:
            key, keep, value = PER_ITEM[metric]
            base_items = {i["id"]: value(i) for i in baseline.get(key, []) if keep(i)}
            cand_items = {i["id"]: value(i) for i in candidate.get(key, []) if keep(i)}
            pairs = [
                (b, c)
                for item_id, b in base_items.items()
                if b is not None and (c := cand_items.get(item_id)) is not None
            ]
            n = len(pairs)
            if n >= 2:
                low, high = paired_bootstrap([c - b for b, c in pairs])
                ci = (round(low, 4), round(high, 4))
                better_up = metric not in LOWER_IS_BETTER
                if low > 0:
                    verdict = "improved" if better_up else "regressed"
                elif high < 0:
                    verdict = "regressed" if better_up else "improved"
                else:
                    verdict = "no significant change"
        rows.append(Row(metric, base_mean, cand_mean, delta, ci, n, verdict))
    base_lat, cand_lat = baseline.get("latency_ms"), candidate.get("latency_ms")
    if base_lat and cand_lat:
        for q in ("p50", "p95"):
            rows.append(
                Row(f"latency_{q}_ms", base_lat[q], cand_lat[q], cand_lat[q] - base_lat[q], None, 0, "aggregate only")
            )
    return rows


def to_markdown(title: str, rows: list[Row]) -> str:
    def fmt(value: float | None) -> str:
        return "—" if value is None else (f"{value:.0f}" if abs(value) >= 10 else f"{value:.3f}")

    lines = [
        f"### {title}",
        "",
        "| Metric | Baseline | Candidate | Δ | paired 95% CI of Δ | n | Verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        ci = f"[{row.ci[0]:+.3f}, {row.ci[1]:+.3f}]" if row.ci else "—"
        delta = "—" if row.delta is None else f"{row.delta:+.3f}" if abs(row.delta) < 10 else f"{row.delta:+.0f}"
        lines.append(
            f"| {row.metric} | {fmt(row.baseline)} | {fmt(row.candidate)} | {delta} | {ci} | {row.n or '—'} | "
            f"{row.verdict} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", type=Path, nargs="+", help="one report (compare configs) or two (before/after)")
    parser.add_argument("--baseline", required=True, help="configuration name in the (first) report")
    parser.add_argument("--candidate", required=True, help="configuration name in the (second) report")
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args()
    if len(args.reports) > 2:
        parser.error("give one or two reports")
    first = json.loads(args.reports[0].read_text(encoding="utf-8"))
    second = json.loads(args.reports[-1].read_text(encoding="utf-8"))
    rows = compare(first["results"][args.baseline], second["results"][args.candidate])
    run = second["evaluation_run"]
    title = f"{args.baseline} → {args.candidate} · {run['dataset']} v{run['dataset_version']} ({run['split']})"
    print(to_markdown(title, rows))
    if args.fail_on_regression and any(r.verdict == "regressed" for r in rows):
        sys.exit(1)


if __name__ == "__main__":
    main()
