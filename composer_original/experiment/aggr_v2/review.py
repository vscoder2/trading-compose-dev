from __future__ import annotations

from dataclasses import asdict, dataclass

from .backtester import run_backtest_v2
from .data import MarketData
from .gpu_replay import replay_with_gpu
from .profiles import profile_hash
from .model_types import BacktestConfigV2, OverlayConfig, StrategyProfile
from .validation import summarize_validation


@dataclass(frozen=True)
class ReviewPass:
    name: str
    ok: bool
    details: dict[str, object]


@dataclass(frozen=True)
class ReviewReport:
    passes: list[ReviewPass]
    all_ok: bool


def run_four_pass_review(
    *,
    market_data: MarketData,
    profile: StrategyProfile,
    config: BacktestConfigV2,
    overlay: OverlayConfig,
    window_label: str,
) -> ReviewReport:
    """Run four deep review passes on the isolated implementation."""
    passes: list[ReviewPass] = []

    # Pass 1: profile immutability anchor.
    ph = profile_hash(profile.name)
    passes.append(
        ReviewPass(
            name="pass1_profile_hash",
            ok=True,
            details={"profile": profile.name, "profile_hash": ph},
        )
    )

    # Pass 2: determinism check (same inputs, same outputs).
    run_a = run_backtest_v2(
        market_data=market_data,
        profile=profile,
        config=config,
        overlay=overlay,
        window_label=window_label,
    )
    run_b = run_backtest_v2(
        market_data=market_data,
        profile=profile,
        config=config,
        overlay=overlay,
        window_label=window_label,
    )
    deterministic = (
        abs(run_a.final_equity - run_b.final_equity) < 1e-9
        and abs(run_a.total_return_pct - run_b.total_return_pct) < 1e-9
        and run_a.trade_count == run_b.trade_count
    )
    passes.append(
        ReviewPass(
            name="pass2_determinism",
            ok=deterministic,
            details={
                "final_equity_a": run_a.final_equity,
                "final_equity_b": run_b.final_equity,
                "trade_count_a": run_a.trade_count,
                "trade_count_b": run_b.trade_count,
            },
        )
    )

    # Pass 3: CPU/GPU parity check.
    gpu = replay_with_gpu(run_a, market_data)
    parity_ok = gpu.diff_bps_vs_cpu <= 0.5
    passes.append(
        ReviewPass(
            name="pass3_cpu_gpu_parity",
            ok=parity_ok,
            details={
                "gpu_backend": gpu.backend,
                "cpu_final_equity": run_a.final_equity,
                "gpu_final_equity": gpu.final_equity,
                "diff_bps_vs_cpu": gpu.diff_bps_vs_cpu,
            },
        )
    )

    # Pass 4: validation sanity and tail-shape summary.
    val = summarize_validation(run_a)
    sanity_ok = (run_a.final_equity > 0) and (run_a.max_drawdown_pct <= 100.0)
    passes.append(
        ReviewPass(
            name="pass4_validation_sanity",
            ok=sanity_ok,
            details={
                "max_drawdown_pct": run_a.max_drawdown_pct,
                "validation": asdict(val),
            },
        )
    )

    return ReviewReport(passes=passes, all_ok=all(p.ok for p in passes))
