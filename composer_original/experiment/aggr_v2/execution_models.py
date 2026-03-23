from __future__ import annotations

from dataclasses import dataclass

from .model_types import OhlcBar, StrategyProfile


@dataclass(frozen=True)
class ProfitLockDecision:
    should_exit: bool
    exit_price: float
    reason: str
    trigger_price: float
    trail_stop_price: float


def annualized_rv_pct(closes: list[float]) -> float:
    """Compute annualized realized volatility in percent.

    This mirrors the existing approach used by runtime/verifier tools.
    """
    if len(closes) < 2:
        return 0.0
    returns: list[float] = []
    for i in range(1, len(closes)):
        prev = float(closes[i - 1])
        cur = float(closes[i])
        if prev > 0:
            returns.append(cur / prev - 1.0)
    if not returns:
        return 0.0
    mu = sum(returns) / len(returns)
    var = sum((x - mu) ** 2 for x in returns) / len(returns)
    return 100.0 * (var**0.5) * (252.0**0.5)


def threshold_pct_for_day(
    profile: StrategyProfile,
    close_history: dict[str, list[float]],
    *,
    day_index: int,
) -> float:
    """Return adaptive threshold percentage for the current day."""
    base = float(profile.profit_lock_threshold_pct)
    if not profile.enable_profit_lock or not profile.profit_lock_adaptive_threshold:
        return base

    sym = profile.profit_lock_adaptive_symbol
    closes = list(close_history.get(sym, []))
    window = max(2, int(profile.profit_lock_adaptive_rv_window))
    if day_index < window:
        return base

    # Use prior closes only for current-day threshold to avoid lookahead.
    lookback = closes[day_index - window : day_index]
    rv = annualized_rv_pct(lookback)
    baseline = max(1e-9, float(profile.profit_lock_adaptive_rv_baseline_pct))
    ratio = rv / baseline

    tmin = min(
        float(profile.profit_lock_adaptive_min_threshold_pct),
        float(profile.profit_lock_adaptive_max_threshold_pct),
    )
    tmax = max(
        float(profile.profit_lock_adaptive_min_threshold_pct),
        float(profile.profit_lock_adaptive_max_threshold_pct),
    )
    return min(tmax, max(tmin, base * ratio))


def profit_lock_decision(
    *,
    prev_close: float,
    bar: OhlcBar,
    threshold_pct: float,
    trail_pct: float,
    mode: str,
) -> ProfitLockDecision:
    """Evaluate whether profit-lock should exit under a given execution model.

    Modes:
    - synthetic: optimistic daily synthetic fill at trailing stop once touched.
    - paper_live_style_optimistic: optimistic intraday-like behavior on daily bars.
    - realistic_close: conservative close-based execution.
    """
    trigger_price = prev_close * (1.0 + threshold_pct / 100.0)
    if bar.high < trigger_price:
        return ProfitLockDecision(False, 0.0, "not_armed", trigger_price, 0.0)

    trail_stop = bar.high * (1.0 - trail_pct / 100.0)
    m = mode.strip().lower()

    # Synthetic: assume idealized fill exactly on trail stop if day range crossed it.
    if m == "synthetic":
        if bar.low <= trail_stop:
            return ProfitLockDecision(True, trail_stop, "synthetic_trail_touch", trigger_price, trail_stop)
        return ProfitLockDecision(False, 0.0, "armed_no_trail_touch", trigger_price, trail_stop)

    # Paper/live style optimistic: use trail-stop touch first, then conservative
    # close fallback if close already below stop.
    if m == "paper_live_style_optimistic":
        if bar.low <= trail_stop:
            return ProfitLockDecision(True, trail_stop, "optimistic_trail_touch", trigger_price, trail_stop)
        if bar.close <= trail_stop:
            return ProfitLockDecision(True, bar.close, "optimistic_close_below_trail", trigger_price, trail_stop)
        return ProfitLockDecision(False, 0.0, "armed_hold", trigger_price, trail_stop)

    # realistic_close: only let close-based exits happen.
    if m == "realistic_close":
        if bar.close <= trail_stop:
            return ProfitLockDecision(True, bar.close, "close_below_trail", trigger_price, trail_stop)
        return ProfitLockDecision(False, 0.0, "armed_hold_close_model", trigger_price, trail_stop)

    raise ValueError(f"Unsupported profit lock execution model: {mode}")


def execution_price(mid_price: float, side: str, slippage_bps: float) -> float:
    """Apply directional slippage.

    Buy trades pay up; sell trades execute lower than mid.
    """
    slip = max(0.0, float(slippage_bps)) / 10_000.0
    if side == "buy":
        return mid_price * (1.0 + slip)
    return mid_price * (1.0 - slip)


def sell_fee(notional: float, sell_fee_bps: float, side: str) -> float:
    if side != "sell":
        return 0.0
    return abs(notional) * max(0.0, float(sell_fee_bps)) / 10_000.0
