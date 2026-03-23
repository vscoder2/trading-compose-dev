from __future__ import annotations

from typing import Callable
from typing import Sequence

from soxl_growth.composer_port.nodes import AssetNode, Context, FilterSelectNode, IfElseNode, Node
from soxl_growth.indicators.drawdown import max_drawdown_percent
from soxl_growth.indicators.returns import cumulative_return_percent
from soxl_growth.indicators.rsi import rsi_base, rsi_smoothed
from soxl_growth.indicators.volatility import stdev_return_annualized_percent
from soxl_growth.logging_setup import get_logger
from soxl_growth.portfolio.target_weights import aggregate_leaf_picks
from soxl_growth.types import Weights

logger = get_logger(__name__)


class InsufficientDataError(ValueError):
    pass


RsiValueFn = Callable[[Sequence[float], int], float | None]


def _required(value: float | None, label: str) -> float:
    if value is None:
        raise InsufficientDataError(f"Insufficient history for {label}")
    return float(value)


def _mdd(ctx: Context, symbol: str, window: int) -> float:
    return _required(max_drawdown_percent(ctx.close_series(symbol), window), f"mdd({symbol},{window})")


def _stdev(ctx: Context, symbol: str, window: int) -> float:
    return _required(
        stdev_return_annualized_percent(ctx.close_series(symbol), window),
        f"stdev({symbol},{window})",
    )


def _cumret(ctx: Context, symbol: str, window: int) -> float:
    return _required(
        cumulative_return_percent(ctx.close_series(symbol), window),
        f"cumret({symbol},{window})",
    )


def _rsi(ctx: Context, symbol: str, window: int) -> float:
    return _required(rsi_base(ctx.close_series(symbol), window), f"rsi({symbol},{window})")


def _metric_cumret(window: int):
    def metric(symbol: str, ctx: Context) -> float:
        return _cumret(ctx, symbol, window)

    return metric


def build_tree(rsi_value_fn: RsiValueFn = rsi_base) -> Node:
    if rsi_value_fn is rsi_base:
        rsi_metric = _rsi
    else:
        rsi_metric = lambda ctx, symbol, window: _required(  # noqa: E731
            rsi_value_fn(ctx.close_series(symbol), window),
            f"rsi({symbol},{window})",
        )

    soxl = AssetNode("SOXL")
    soxs = AssetNode("SOXS")
    spxl = AssetNode("SPXL")

    top2_growth = FilterSelectNode(
        metric=_metric_cumret(21),
        mode="top",
        k=2,
        assets=["SOXL", "TQQQ", "SPXL"],
    )

    bottom2_hedge = FilterSelectNode(
        metric=_metric_cumret(3),
        mode="bottom",
        k=2,
        assets=["TMV", "SQQQ", "SPXS", "SPXS"],
    )

    top3_growth = FilterSelectNode(
        metric=_metric_cumret(21),
        mode="top",
        k=3,
        assets=["SOXL", "TQQQ", "TMF", "SPXL"],
    )

    top2_normal = FilterSelectNode(
        metric=_metric_cumret(21),
        mode="top",
        k=2,
        assets=["SOXL", "SPXL", "TQQQ"],
    )

    crash_stdev14_le18 = IfElseNode(
        cond=lambda ctx: _stdev(ctx, "TQQQ", 100) <= 3.8,
        then_branch=top2_growth,
        else_branch=IfElseNode(
            cond=lambda ctx: rsi_metric(ctx, "TQQQ", 30) >= 50.0,
            then_branch=IfElseNode(
                cond=lambda ctx: _stdev(ctx, "TQQQ", 30) >= 5.8,
                then_branch=soxs,
                else_branch=spxl,
            ),
            else_branch=IfElseNode(
                cond=lambda ctx: _cumret(ctx, "TQQQ", 8) <= -20.0,
                then_branch=soxl,
                else_branch=IfElseNode(
                    cond=lambda ctx: _mdd(ctx, "TQQQ", 200) <= 65.0,
                    then_branch=bottom2_hedge,
                    else_branch=soxl,
                ),
            ),
        ),
    )

    crash_stdev14_gt18 = IfElseNode(
        cond=lambda ctx: _cumret(ctx, "TQQQ", 30) <= -10.0,
        then_branch=bottom2_hedge,
        else_branch=top3_growth,
    )

    crash_branch = IfElseNode(
        cond=lambda ctx: _stdev(ctx, "TQQQ", 14) <= 18.0,
        then_branch=crash_stdev14_le18,
        else_branch=crash_stdev14_gt18,
    )

    normal_rsi32_le = IfElseNode(
        cond=lambda ctx: _stdev(ctx, "SOXL", 105) <= 4.9226,
        then_branch=soxl,
        else_branch=IfElseNode(
            cond=lambda ctx: rsi_metric(ctx, "SOXL", 30) >= 57.49,
            then_branch=IfElseNode(
                cond=lambda ctx: _stdev(ctx, "SOXL", 30) >= 5.4135,
                then_branch=soxs,
                else_branch=top2_normal,
            ),
            else_branch=IfElseNode(
                cond=lambda ctx: _cumret(ctx, "SOXL", 32) <= -12.0,
                then_branch=soxl,
                else_branch=IfElseNode(
                    cond=lambda ctx: _mdd(ctx, "SOXL", 250) <= 71.0,
                    then_branch=soxs,
                    else_branch=soxl,
                ),
            ),
        ),
    )

    normal_rsi32_gt = IfElseNode(
        cond=lambda ctx: rsi_metric(ctx, "SOXL", 32) >= 50.0,
        then_branch=soxs,
        else_branch=top3_growth,
    )

    return IfElseNode(
        cond=lambda ctx: _mdd(ctx, "SOXL", 60) >= 50.0,
        then_branch=crash_branch,
        else_branch=IfElseNode(
            cond=lambda ctx: rsi_metric(ctx, "SOXL", 32) <= 62.1995,
            then_branch=normal_rsi32_le,
            else_branch=normal_rsi32_gt,
        ),
    )


SOXL_GROWTH_V245_RL_TREE = build_tree()


def build_smoothed_rsi_tree(smoothing_span: int) -> Node:
    """Build the strategy tree using optional RSI smoothing for parity sweeps."""
    return build_tree(lambda close, window: rsi_smoothed(close, window, smoothing_span=smoothing_span))


def evaluate_strategy(ctx: Context, tree: Node | None = None) -> Weights:
    active_tree = tree if tree is not None else SOXL_GROWTH_V245_RL_TREE
    picks = active_tree.eval(ctx)
    weights = aggregate_leaf_picks(picks)
    logger.debug("Strategy evaluated picks=%s weights=%s", picks, weights)
    return weights
