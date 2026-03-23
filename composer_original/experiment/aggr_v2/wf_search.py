from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from statistics import mean

from .candidate_grid import Candidate
from .data import MarketData
from .model_types import BacktestResultV2, WindowSpec
from .runner_utils import run_window_backtest
from .validation import summarize_validation, walk_forward_splits


@dataclass(frozen=True)
class FoldMetric:
    fold_id: int
    start: str
    end: str
    return_pct: float
    maxdd_pct: float
    sharpe: float
    sortino: float
    calmar: float
    risk_score: float


@dataclass(frozen=True)
class CandidateScore:
    candidate_name: str
    profile_name: str
    window_avg_return_pct: float
    window_avg_risk_score: float
    scenario_avg_return_pct: float
    scenario_avg_risk_score: float
    wf_avg_return_pct: float
    wf_avg_risk_score: float
    overall_score: float
    accepted: bool
    acceptance_reason: str
    details: dict[str, object]


def _risk_score(sharpe: float, sortino: float, calmar: float, maxdd_pct: float) -> float:
    """Robust clipped risk-adjusted score for candidate ranking."""
    s = max(-10.0, min(10.0, float(sharpe)))
    so = max(-10.0, min(10.0, float(sortino)))
    c = max(-10.0, min(10.0, float(calmar)))
    dd_penalty = max(0.0, float(maxdd_pct)) / 50.0
    return 0.5 * s + 0.25 * so + 0.25 * c - dd_penalty


def evaluate_windows(
    *,
    market_data: MarketData,
    candidate: Candidate,
    profile_name: str,
    windows: list[WindowSpec],
) -> tuple[list[dict[str, object]], float, float]:
    rows: list[dict[str, object]] = []
    rets: list[float] = []
    risks: list[float] = []

    for w in windows:
        result = run_window_backtest(
            market_data=market_data,
            profile_name=profile_name,
            config=candidate.config,
            overlay=candidate.overlay,
            window=w,
        )
        val = summarize_validation(result)
        score = _risk_score(val.sharpe, val.sortino, val.calmar, result.max_drawdown_pct)
        rows.append(
            {
                "window": w.label,
                "start": w.start.isoformat(),
                "end": w.end.isoformat(),
                "return_pct": result.total_return_pct,
                "maxdd_pct": result.max_drawdown_pct,
                "sharpe": val.sharpe,
                "sortino": val.sortino,
                "calmar": val.calmar,
                "risk_score": score,
            }
        )
        rets.append(result.total_return_pct)
        risks.append(score)

    return rows, mean(rets) if rets else 0.0, mean(risks) if risks else 0.0


def evaluate_walk_forward(
    *,
    market_data: MarketData,
    candidate: Candidate,
    profile_name: str,
    train_days: int,
    test_days: int,
    step_days: int,
) -> tuple[list[FoldMetric], float, float]:
    days = market_data.days
    splits = walk_forward_splits(days, train_days=train_days, test_days=test_days, step_days=step_days)
    folds: list[FoldMetric] = []

    for idx, (_, train_end, test_start, test_end) in enumerate(splits):
        # Run only on test segment, with explicit warmup extension handled by runner.
        w = WindowSpec(
            label=f"wf_fold_{idx}",
            start=days[test_start],
            end=days[test_end - 1],
        )
        result = run_window_backtest(
            market_data=market_data,
            profile_name=profile_name,
            config=candidate.config,
            overlay=candidate.overlay,
            window=w,
        )
        val = summarize_validation(result, bootstrap_draws=250, bootstrap_block_len=10)
        score = _risk_score(val.sharpe, val.sortino, val.calmar, result.max_drawdown_pct)
        folds.append(
            FoldMetric(
                fold_id=idx,
                start=w.start.isoformat(),
                end=w.end.isoformat(),
                return_pct=result.total_return_pct,
                maxdd_pct=result.max_drawdown_pct,
                sharpe=val.sharpe,
                sortino=val.sortino,
                calmar=val.calmar,
                risk_score=score,
            )
        )

    if not folds:
        return [], 0.0, 0.0
    return folds, mean(f.return_pct for f in folds), mean(f.risk_score for f in folds)


def score_candidates_strict(
    *,
    market_data: MarketData,
    profile_name: str,
    candidates: list[Candidate],
    windows: list[WindowSpec],
    scenarios: list[WindowSpec],
    wf_train_days: int = 252,
    wf_test_days: int = 63,
    wf_step_days: int = 21,
) -> list[CandidateScore]:
    """Score and rank candidates with strict dual acceptance gates.

    Hard acceptance rule:
    candidate must beat baseline on BOTH
    - average return
    - average risk-adjusted score
    across multi-window + scenario + walk-forward views.
    """
    if not candidates:
        return []

    # Baseline is required and should be first in provided list.
    baseline = None
    for c in candidates:
        if c.name == "baseline":
            baseline = c
            break
    if baseline is None:
        raise ValueError("Candidate list must include a 'baseline' candidate")

    # Evaluate baseline once.
    base_win_rows, base_win_ret, base_win_risk = evaluate_windows(
        market_data=market_data,
        candidate=baseline,
        profile_name=profile_name,
        windows=windows,
    )
    base_scn_rows, base_scn_ret, base_scn_risk = evaluate_windows(
        market_data=market_data,
        candidate=baseline,
        profile_name=profile_name,
        windows=scenarios,
    )
    base_wf_folds, base_wf_ret, base_wf_risk = evaluate_walk_forward(
        market_data=market_data,
        candidate=baseline,
        profile_name=profile_name,
        train_days=wf_train_days,
        test_days=wf_test_days,
        step_days=wf_step_days,
    )

    baseline_bundle = {
        "window_avg_return_pct": base_win_ret,
        "window_avg_risk_score": base_win_risk,
        "scenario_avg_return_pct": base_scn_ret,
        "scenario_avg_risk_score": base_scn_risk,
        "wf_avg_return_pct": base_wf_ret,
        "wf_avg_risk_score": base_wf_risk,
        "window_rows": base_win_rows,
        "scenario_rows": base_scn_rows,
        "wf_folds": [asdict(f) for f in base_wf_folds],
    }

    scores: list[CandidateScore] = []
    for c in candidates:
        win_rows, win_ret, win_risk = evaluate_windows(
            market_data=market_data,
            candidate=c,
            profile_name=profile_name,
            windows=windows,
        )
        scn_rows, scn_ret, scn_risk = evaluate_windows(
            market_data=market_data,
            candidate=c,
            profile_name=profile_name,
            windows=scenarios,
        )
        wf_folds, wf_ret, wf_risk = evaluate_walk_forward(
            market_data=market_data,
            candidate=c,
            profile_name=profile_name,
            train_days=wf_train_days,
            test_days=wf_test_days,
            step_days=wf_step_days,
        )

        # Multi-view average score.
        overall = mean([win_ret, scn_ret, wf_ret]) + 2.0 * mean([win_risk, scn_risk, wf_risk])

        if c.name == "baseline":
            accepted = True
            reason = "baseline_anchor"
        else:
            # Strict hard acceptance rule (both must improve).
            ret_gate = (win_ret > base_win_ret) and (scn_ret > base_scn_ret) and (wf_ret > base_wf_ret)
            risk_gate = (win_risk > base_win_risk) and (scn_risk > base_scn_risk) and (wf_risk > base_wf_risk)
            accepted = bool(ret_gate and risk_gate)
            reason = f"ret_gate={ret_gate},risk_gate={risk_gate}"

        scores.append(
            CandidateScore(
                candidate_name=c.name,
                profile_name=profile_name,
                window_avg_return_pct=win_ret,
                window_avg_risk_score=win_risk,
                scenario_avg_return_pct=scn_ret,
                scenario_avg_risk_score=scn_risk,
                wf_avg_return_pct=wf_ret,
                wf_avg_risk_score=wf_risk,
                overall_score=overall,
                accepted=accepted,
                acceptance_reason=reason,
                details={
                    "window_rows": win_rows,
                    "scenario_rows": scn_rows,
                    "wf_folds": [asdict(f) for f in wf_folds],
                    "baseline": baseline_bundle,
                },
            )
        )

    # Rank accepted first, then by overall score descending.
    scores.sort(key=lambda s: (0 if s.accepted else 1, -s.overall_score))
    return scores
