"""Fusing a vector ranking and a keyword ranking into one.

The two legs score on different scales (cosine similarity against BM25, say), so their raw scores
cannot be added. Reciprocal rank fusion sidesteps that by only looking at positions; min-max fusion
keeps the scores but squashes each leg onto [0, 1] first. Both are pure and deterministic, so the
benchmark and production call the same code and get the same order.
"""

from collections import defaultdict


def reciprocal_rank_fusion(
    rankings: list[list[str]], k: int
) -> list[tuple[str, float]]:
    # an id's score is the sum of 1 / (k + rank) over every ranking it appears in. the rank is its
    # 1-based position, and a repeat within one ranking is skipped, so a duplicate cannot earn a
    # second contribution from the same leg
    scores: dict[str, float] = {}
    for ranking in rankings:
        seen: set[str] = set()
        for position, doc_id in enumerate(ranking, start=1):
            if doc_id in seen:
                continue
            seen.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + position)
    # dict order is first appearance, and sorted is stable, so ties keep that order
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


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
