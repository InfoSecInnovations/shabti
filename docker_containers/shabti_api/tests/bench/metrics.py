"""Standard IR metrics over ranked lists of document ids, plus a paired significance test.

Pure standard library on purpose: the benchmark only needs a handful of metrics, and a
third-party evaluation package is not worth the dependency.
"""

from __future__ import annotations

import math
import random


def dedupe(ranked: list[str]) -> list[str]:
    # several chunks of one document can appear in a ranking, so only the first hit of each doc counts
    seen: set[str] = set()
    unique: list[str] = []
    for doc_id in ranked:
        if doc_id not in seen:
            seen.add(doc_id)
            unique.append(doc_id)
    return unique


def hit_at_k(ranked: list[str], relevant: dict[str, int], k: int) -> float:
    # uses the raw list on purpose: a relevant chunk in the window is a hit even if its doc repeats
    return float(any(relevant.get(doc_id, 0) > 0 for doc_id in ranked[:k]))


def recall_at_k(ranked: list[str], relevant: dict[str, int], k: int) -> float:
    total = sum(1 for grade in relevant.values() if grade > 0)
    if total == 0:
        return 0.0
    found = sum(1 for doc_id in dedupe(ranked)[:k] if relevant.get(doc_id, 0) > 0)
    return found / total


def ndcg_at_k(ranked: list[str], relevant: dict[str, int], k: int) -> float:
    # gain 2^g - 1 rewards highly graded docs more than merely relevant ones
    gains = [relevant.get(doc_id, 0) for doc_id in dedupe(ranked)[:k]]
    dcg = sum(
        (2**grade - 1) / math.log2(i + 1) for i, grade in enumerate(gains, start=1)
    )

    # the ideal ranking puts every relevant doc in grade order, so it is the best achievable dcg
    ideal = sorted((grade for grade in relevant.values() if grade > 0), reverse=True)[
        :k
    ]
    idcg = sum(
        (2**grade - 1) / math.log2(i + 1) for i, grade in enumerate(ideal, start=1)
    )
    if idcg == 0:
        return 0.0
    return dcg / idcg


def mrr_at_k(ranked: list[str], relevant: dict[str, int], k: int) -> float:
    for rank, doc_id in enumerate(dedupe(ranked)[:k], start=1):
        if relevant.get(doc_id, 0) > 0:
            return 1.0 / rank
    return 0.0


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    # linear interpolation between the two closest ranks, matching numpy's default method
    position = (p / 100) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def paired_randomization_test(
    a: list[float], b: list[float], trials: int = 10000, seed: int = 0
) -> float:
    if len(a) != len(b):
        raise ValueError(
            f"paired runs must have equal length, got {len(a)} and {len(b)}"
        )
    diffs = [x - y for x, y in zip(a, b)]
    if not diffs or all(d == 0 for d in diffs):
        return 1.0

    observed = abs(mean(diffs))
    rng = random.Random(seed)
    # under the null hypothesis each pair's sign is arbitrary, so flipping signs at random
    # builds the distribution of the mean difference we would see if the runs were equivalent
    at_least_as_extreme = 0
    for _ in range(trials):
        flipped = [d if rng.random() < 0.5 else -d for d in diffs]
        # the small tolerance stops floating point noise from hiding a tie with the observed mean
        if abs(mean(flipped)) >= observed - 1e-12:
            at_least_as_extreme += 1
    # the +1 on both sides keeps the p-value from ever being exactly zero
    return (at_least_as_extreme + 1) / (trials + 1)
