from __future__ import annotations

from datetime import date, timedelta

from .model_types import WindowSpec


WINDOW_TO_DAYS = {
    "10d": 14,
    "1m": 31,
    "2m": 62,
    "3m": 93,
    "4m": 124,
    "5m": 155,
    "6m": 186,
    "9m": 279,
    "1y": 366,
    "2y": 731,
    "3y": 1096,
    "4y": 1461,
    "5y": 1826,
    "7y": 2557,
    "10y": 3653,
}


def resolve_windows(end_day: date, labels: list[str]) -> list[WindowSpec]:
    out: list[WindowSpec] = []
    for label in labels:
        key = label.strip().lower()
        if key not in WINDOW_TO_DAYS:
            valid = ", ".join(sorted(WINDOW_TO_DAYS))
            raise ValueError(f"Unsupported window '{label}'. Valid windows: {valid}")
        delta = int(WINDOW_TO_DAYS[key])
        start = end_day - timedelta(days=delta)
        out.append(WindowSpec(label=key, start=start, end=end_day))
    return out
