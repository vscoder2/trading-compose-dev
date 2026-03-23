from __future__ import annotations

from soxl_growth.types import LeafPick


def select_assets(
    assets: list[str],
    metric: callable,
    k: int,
    mode: str,
) -> list[LeafPick]:
    if mode not in {"top", "bottom"}:
        raise ValueError("mode must be 'top' or 'bottom'")
    if k <= 0:
        return []

    scored: list[tuple[float, int, str]] = []
    for idx, symbol in enumerate(assets):
        scored.append((float(metric(symbol)), idx, symbol))

    if mode == "top":
        ranked = sorted(scored, key=lambda x: (-x[0], x[1]))
    else:
        ranked = sorted(scored, key=lambda x: (x[0], x[1]))

    selected = ranked[:k]
    if not selected:
        return []

    weight = 1.0 / len(selected)
    return [(symbol, weight) for (_, _, symbol) in selected]
