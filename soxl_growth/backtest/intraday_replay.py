from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from soxl_growth.config import OverlayConfig
from soxl_growth.logging_setup import get_logger
from soxl_growth.overlay.overlay_state_machine import OverlayMetrics, OverlaySnapshot, OverlayState, OverlayStateMachine
from soxl_growth.types import Weights

logger = get_logger(__name__)


@dataclass(frozen=True)
class ReplayConfig:
    eval_minutes: int = 5
    lookback_minutes: int = 75


@dataclass(frozen=True)
class ReplayPoint:
    timestamp: datetime
    state: str
    reason: str
    dd_intra_soxl: float
    rv_15m_tqqq: float
    target: Weights


@dataclass(frozen=True)
class ReplayResult:
    points: list[ReplayPoint]
    flips: int
    hedge_points: int
    kill_switch_points: int


def _rolling_returns(values: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        cur = values[i]
        if prev > 0:
            out.append(cur / prev - 1.0)
    return out


def _annualized_vol_from_returns(returns: list[float]) -> float:
    if not returns:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((x - mean) ** 2 for x in returns) / len(returns)
    return (var ** 0.5) * (252 * 390) ** 0.5 * 100.0


def _downsample_close(values: list[float], step: int) -> list[float]:
    if step <= 0:
        raise ValueError("step must be positive")
    return [values[i] for i in range(step - 1, len(values), step)]


def _ema(values: list[float], span: int) -> float | None:
    if not values:
        return None
    alpha = 2.0 / (span + 1.0)
    cur = values[0]
    for x in values[1:]:
        cur = alpha * x + (1.0 - alpha) * cur
    return cur


def _compute_fade_confirmation(soxl_minute_closes: list[float]) -> bool:
    closes_15m = _downsample_close(soxl_minute_closes, 15)
    closes_5m = _downsample_close(soxl_minute_closes, 5)
    if len(closes_15m) < 20 or len(closes_5m) < 13:
        return False
    close_15m = closes_15m[-1]
    ema_15m_20 = _ema(closes_15m[-20:], span=20)
    if ema_15m_20 is None:
        return False
    cumret_5m_12 = 100.0 * (closes_5m[-1] / closes_5m[-13] - 1.0)
    return (close_15m < ema_15m_20) and (cumret_5m_12 < 0.0)


def _select_overlay_target(minute_closes: dict[str, list[float]]) -> dict[str, float]:
    defensive = ["SOXS", "SQQQ", "SPXS", "TMF"]
    scores: list[tuple[float, str]] = []
    for symbol in defensive:
        closes = minute_closes.get(symbol, [])
        if len(closes) < 2 or closes[0] <= 0:
            scores.append((-1e9, symbol))
            continue
        score = 100.0 * (closes[-1] / closes[0] - 1.0)
        scores.append((score, symbol))
    scores.sort(reverse=True)
    return {scores[0][1]: 1.0}


def run_intraday_overlay_replay(
    *,
    minute_history: dict[str, list[tuple[datetime, float]]],
    baseline_target_by_day: dict[date, Weights],
    overbought_fade_by_day: dict[date, bool] | None = None,
    replay_config: ReplayConfig | None = None,
    overlay_config: OverlayConfig | None = None,
) -> ReplayResult:
    """Replay overlay state transitions on minute data.

    This replay focuses on state/target behavior and flip counts, not execution PnL.
    """
    replay_cfg = replay_config or ReplayConfig()
    overlay_cfg = overlay_config or OverlayConfig()
    overbought_map = overbought_fade_by_day or {}
    sm = OverlayStateMachine(overlay_cfg)

    # Flatten into timestamp-indexed updates.
    updates: dict[datetime, dict[str, float]] = defaultdict(dict)
    for symbol, rows in minute_history.items():
        for ts, close in rows:
            updates[ts][symbol] = float(close)

    timestamps = sorted(updates)
    windows: dict[str, list[tuple[datetime, float]]] = defaultdict(list)

    points: list[ReplayPoint] = []
    flips = 0
    flip_count_today = 0
    last_trade_ts: datetime | None = None
    last_day: date | None = None
    previous_target: Weights = {}

    for ts in timestamps:
        day = ts.date()
        if last_day is None or day != last_day:
            flip_count_today = 0
            last_day = day

        for symbol, close in updates[ts].items():
            windows[symbol].append((ts, close))
            # Keep only replay lookback horizon.
            while windows[symbol] and (ts - windows[symbol][0][0]).total_seconds() > replay_cfg.lookback_minutes * 60:
                windows[symbol].pop(0)

        if ts.minute % replay_cfg.eval_minutes != 0:
            continue

        soxl_closes = [p for _, p in windows.get("SOXL", [])]
        tqqq_closes = [p for _, p in windows.get("TQQQ", [])]
        if len(soxl_closes) < 2 or len(tqqq_closes) < 2:
            continue

        high_water_mark = max(soxl_closes[-60:]) if len(soxl_closes) >= 60 else max(soxl_closes)
        dd_intra = 100.0 * (high_water_mark - soxl_closes[-1]) / high_water_mark
        rv_15m = _annualized_vol_from_returns(_rolling_returns(tqqq_closes[-15:]))

        minute_closes = {symbol: [p for _, p in rows] for symbol, rows in windows.items()}
        overlay_target = _select_overlay_target(minute_closes)
        baseline_target = baseline_target_by_day.get(day, {})
        overbought_flag = bool(overbought_map.get(day, False))
        fade_confirmed = _compute_fade_confirmation(soxl_closes)

        if last_trade_ts is None:
            minutes_since_last_trade = 10_000
        else:
            minutes_since_last_trade = int((ts - last_trade_ts).total_seconds() // 60)

        metrics = OverlayMetrics(
            dd_intra_soxl=dd_intra,
            rv_15m_tqqq=rv_15m,
            price_soxl=soxl_closes[-1],
            vwap_60m_soxl=sum(soxl_closes[-60:]) / len(soxl_closes[-60:]),
            rsi_15m_soxl=50.0,
            realized_pnl_pct=0.0,
            data_failure_minutes=0,
            margin_usage=0.0,
            overbought_fade_regime=overbought_flag,
            fade_confirmed=fade_confirmed,
        )
        snapshot = OverlaySnapshot(
            flip_count_today=flip_count_today,
            minutes_since_last_trade=minutes_since_last_trade,
        )
        step = sm.step(metrics, snapshot, baseline_target, overlay_target)

        points.append(
            ReplayPoint(
                timestamp=ts,
                state=str(step.state),
                reason=step.reason,
                dd_intra_soxl=dd_intra,
                rv_15m_tqqq=rv_15m,
                target=step.target,
            )
        )

        if step.target != previous_target and step.target:
            flips += 1
            flip_count_today += 1
            last_trade_ts = ts
            sm.on_trade_executed()
            previous_target = step.target

    hedge_points = sum(1 for p in points if OverlayState.OVERLAY_HEDGE.value in p.state)
    kill_switch_points = sum(1 for p in points if OverlayState.KILL_SWITCH.value in p.state)

    logger.info(
        "Overlay replay complete points=%d flips=%d hedge_points=%d kill_switch_points=%d",
        len(points),
        flips,
        hedge_points,
        kill_switch_points,
    )
    return ReplayResult(
        points=points,
        flips=flips,
        hedge_points=hedge_points,
        kill_switch_points=kill_switch_points,
    )
