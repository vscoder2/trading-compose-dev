from __future__ import annotations

from collections import defaultdict

from soxl_growth.types import LeafPick, Weights


def normalize_weights(weights: Weights) -> Weights:
    cleaned = {k: float(v) for k, v in weights.items() if float(v) != 0.0}
    total = sum(cleaned.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in cleaned.items()}


def aggregate_leaf_picks(picks: list[LeafPick]) -> Weights:
    agg: defaultdict[str, float] = defaultdict(float)
    for sym, weight in picks:
        agg[sym] += float(weight)
    return normalize_weights(dict(agg))
