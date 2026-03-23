from __future__ import annotations

import math
from datetime import date


def max_drawdown_pct(equity_curve: list[tuple[date, float]]) -> float:
    peak = -math.inf
    max_dd = 0.0
    for _, eq in equity_curve:
        peak = max(peak, eq)
        if peak > 0:
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
    return 100.0 * max_dd


def cagr_pct(equity_curve: list[tuple[date, float]]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    start_day, start_eq = equity_curve[0]
    end_day, end_eq = equity_curve[-1]
    if start_eq <= 0 or end_eq <= 0:
        return 0.0
    years = max((end_day - start_day).days / 365.25, 1e-9)
    return 100.0 * ((end_eq / start_eq) ** (1.0 / years) - 1.0)


def daily_returns(equity_curve: list[tuple[date, float]]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1][1]
        cur = equity_curve[i][1]
        if prev > 0:
            out.append(cur / prev - 1.0)
    return out


def sharpe_ratio(equity_curve: list[tuple[date, float]]) -> float:
    rets = daily_returns(equity_curve)
    if not rets:
        return 0.0
    mu = sum(rets) / len(rets)
    var = sum((x - mu) ** 2 for x in rets) / len(rets)
    sd = var**0.5
    if sd <= 0:
        return 0.0
    return (mu / sd) * (252.0**0.5)


def sortino_ratio(equity_curve: list[tuple[date, float]]) -> float:
    rets = daily_returns(equity_curve)
    if not rets:
        return 0.0
    mu = sum(rets) / len(rets)
    downside = [min(0.0, r) for r in rets]
    dvar = sum((x - 0.0) ** 2 for x in downside) / len(downside)
    dsd = dvar**0.5
    if dsd <= 0:
        return 0.0
    return (mu / dsd) * (252.0**0.5)


def calmar_ratio(equity_curve: list[tuple[date, float]]) -> float:
    mdd = max_drawdown_pct(equity_curve)
    if mdd <= 0:
        return 0.0
    return cagr_pct(equity_curve) / mdd
