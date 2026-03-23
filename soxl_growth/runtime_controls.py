from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class SessionWindow:
    open_time: datetime
    close_time: datetime


def is_no_trade_window(
    now: datetime,
    session: SessionWindow,
    no_trade_open_minutes: int,
    no_trade_close_minutes: int,
) -> bool:
    """Return True if `now` falls into configured open/close no-trade windows."""
    open_end = session.open_time + timedelta(minutes=max(0, no_trade_open_minutes))
    close_start = session.close_time - timedelta(minutes=max(0, no_trade_close_minutes))
    in_open_window = session.open_time <= now < open_end
    in_close_window = close_start <= now <= session.close_time
    return in_open_window or in_close_window


def should_run_overnight_flatten(
    now: datetime,
    session: SessionWindow,
    flatten_minutes_before_close: int,
    already_flattened_today: bool,
) -> bool:
    """Return True when flattening should trigger in the close-approach window."""
    if already_flattened_today:
        return False
    trigger_time = session.close_time - timedelta(minutes=max(0, flatten_minutes_before_close))
    return trigger_time <= now < session.close_time


def parse_symbol_csv(text: str) -> set[str]:
    if not text.strip():
        return set()
    return {x.strip().upper() for x in text.split(",") if x.strip()}


def select_positions_to_flatten(
    positions: list[dict[str, object]],
    mode: str,
    leveraged_symbols: set[str],
    allow_overnight_symbols: set[str],
) -> list[str]:
    """Select symbols to flatten under overnight policy.

    Modes:
    - all: flatten every symbol except explicitly allowed.
    - leveraged-only: flatten only leveraged-symbol list except explicitly allowed.
    """
    out: list[str] = []
    for pos in positions:
        symbol = str(pos.get("symbol", "")).upper()
        if not symbol or symbol in allow_overnight_symbols:
            continue

        if mode == "all":
            out.append(symbol)
        elif mode == "leveraged-only":
            if symbol in leveraged_symbols:
                out.append(symbol)
        else:
            raise ValueError(f"Unsupported flatten mode: {mode}")
    return sorted(set(out))
