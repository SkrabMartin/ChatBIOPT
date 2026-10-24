"""Ranking metrics for retrieval evaluation (graded relevance) and a percentile bootstrap for small query sets."""

import math
import random
from collections.abc import Hashable, Mapping, Sequence


def recall_at_k[T: Hashable](ranked: Sequence[T], relevant: Mapping[T, int], k: int) -> float:
    """Share of relevant items (grade > 0) found in the top k."""
    wanted = {item for item, grade in relevant.items() if grade > 0}
    if not wanted:
        raise ValueError("query has no relevant items")
    return len(wanted.intersection(ranked[:k])) / len(wanted)


def reciprocal_rank[T: Hashable](ranked: Sequence[T], relevant: Mapping[T, int], k: int | None = None) -> float:
    for position, item in enumerate(ranked[:k] if k else ranked, start=1):
        if relevant.get(item, 0) > 0:
            return 1.0 / position
    return 0.0


def ndcg_at_k[T: Hashable](ranked: Sequence[T], relevant: Mapping[T, int], k: int) -> float:
    """nDCG with exponential gain (2^grade - 1) and log2 position discount."""

    def dcg(grades: Sequence[int]) -> float:
        return sum(((1 << grade) - 1) / math.log2(position + 1) for position, grade in enumerate(grades, start=1))

    ideal = dcg(sorted((g for g in relevant.values() if g > 0), reverse=True)[:k])
    if ideal == 0:
        raise ValueError("query has no relevant items")
    return dcg([relevant.get(item, 0) for item in ranked[:k]]) / ideal


def bootstrap_ci(
    values: Sequence[float], *, iterations: int = 2000, confidence: float = 0.95, seed: int = 7
) -> tuple[float, float]:
    """Percentile bootstrap CI of the mean. Deterministic for a given seed."""
    if not values:
        raise ValueError("no values")
    rng = random.Random(seed)  # noqa: S311 - statistical resampling, not security
    n = len(values)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(iterations))
    lower = means[int((1 - confidence) / 2 * iterations)]
    upper = means[min(iterations - 1, int((1 + confidence) / 2 * iterations))]
    return lower, upper


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100)."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q / 100 * len(ordered)) - 1)]
