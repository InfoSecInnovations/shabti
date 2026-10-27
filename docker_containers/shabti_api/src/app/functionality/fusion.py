"""Fusing a vector ranking and a keyword ranking into one.

The two legs score on different scales (cosine similarity against BM25, say), so their raw scores
cannot be added. Min-max fusion squashes each leg onto [0, 1] first, then sums the weighted results.
It is pure and deterministic, so the benchmark and production call the same code and get the same
order.
"""

from collections import defaultdict


def min_max_fusion(
    scored: list[list[tuple[str, float]]], weights: list[float]
) -> list[tuple[str, float]]:
    if len(weights) != len(scored):
        raise ValueError(
            f"got {len(weights)} weights for {len(scored)} score lists, need one per list"
        )

    fused: dict[str, float] = defaultdict(float)
    for leg, weight in zip(scored, weights):
        # first occurrence wins within a list, so the normalisation range is built from the same
        # scores that are then contributed
        leg_scores: dict[str, float] = {}
        for doc_id, score in leg:
            leg_scores.setdefault(doc_id, score)
        if not leg_scores:
            continue
        low = min(leg_scores.values())
        high = max(leg_scores.values())
        for doc_id, score in leg_scores.items():
            # a flat list has no spread to normalise against; every entry is equally the best
            normalised = 1.0 if high == low else (score - low) / (high - low)
            fused[doc_id] += weight * normalised
    return sorted(fused.items(), key=lambda item: item[1], reverse=True)
