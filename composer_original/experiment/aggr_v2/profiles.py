from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

from .model_types import StrategyProfile


# Keep symbol universe fixed to the current project assumptions.
UNIVERSE: tuple[str, ...] = (
    "SOXL",
    "SOXS",
    "TQQQ",
    "SQQQ",
    "SPXL",
    "SPXS",
    "TMF",
    "TMV",
)


# Locked profile mirrors. These are independent copies and intentionally do not
# mutate or import runtime globals.
LOCKED_PROFILES: dict[str, StrategyProfile] = {
    "original_composer": StrategyProfile(
        name="original_composer",
        enable_profit_lock=False,
        profit_lock_mode="fixed",
        profit_lock_threshold_pct=15.0,
        profit_lock_trail_pct=5.0,
        profit_lock_adaptive_threshold=False,
        profit_lock_adaptive_symbol="TQQQ",
        profit_lock_adaptive_rv_window=14,
        profit_lock_adaptive_rv_baseline_pct=85.0,
        profit_lock_adaptive_min_threshold_pct=8.0,
        profit_lock_adaptive_max_threshold_pct=30.0,
        intraday_profit_lock_check_minutes=0,
    ),
    "trailing12_4_adapt": StrategyProfile(
        name="trailing12_4_adapt",
        enable_profit_lock=True,
        profit_lock_mode="trailing",
        profit_lock_threshold_pct=12.0,
        profit_lock_trail_pct=4.0,
        profit_lock_adaptive_threshold=True,
        profit_lock_adaptive_symbol="TQQQ",
        profit_lock_adaptive_rv_window=14,
        profit_lock_adaptive_rv_baseline_pct=85.0,
        profit_lock_adaptive_min_threshold_pct=8.0,
        profit_lock_adaptive_max_threshold_pct=30.0,
        intraday_profit_lock_check_minutes=0,
    ),
    "aggr_adapt_t10_tr2_rv14_b85_m8_M30": StrategyProfile(
        name="aggr_adapt_t10_tr2_rv14_b85_m8_M30",
        enable_profit_lock=True,
        profit_lock_mode="trailing",
        profit_lock_threshold_pct=10.0,
        profit_lock_trail_pct=2.0,
        profit_lock_adaptive_threshold=True,
        profit_lock_adaptive_symbol="TQQQ",
        profit_lock_adaptive_rv_window=14,
        profit_lock_adaptive_rv_baseline_pct=85.0,
        profit_lock_adaptive_min_threshold_pct=8.0,
        profit_lock_adaptive_max_threshold_pct=30.0,
        intraday_profit_lock_check_minutes=0,
    ),
    "aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m": StrategyProfile(
        name="aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m",
        enable_profit_lock=True,
        profit_lock_mode="trailing",
        profit_lock_threshold_pct=10.0,
        profit_lock_trail_pct=2.0,
        profit_lock_adaptive_threshold=True,
        profit_lock_adaptive_symbol="TQQQ",
        profit_lock_adaptive_rv_window=14,
        profit_lock_adaptive_rv_baseline_pct=85.0,
        profit_lock_adaptive_min_threshold_pct=8.0,
        profit_lock_adaptive_max_threshold_pct=30.0,
        intraday_profit_lock_check_minutes=5,
    ),
}


def get_profile(name: str) -> StrategyProfile:
    if name not in LOCKED_PROFILES:
        valid = ", ".join(sorted(LOCKED_PROFILES))
        raise KeyError(f"Unknown profile '{name}'. Valid profiles: {valid}")
    return LOCKED_PROFILES[name]


def profile_hash(name: str) -> str:
    """Stable hash for drift detection in review checks."""
    profile = get_profile(name)
    payload = json.dumps(asdict(profile), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
