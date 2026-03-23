from __future__ import annotations

from dataclasses import dataclass

from .model_types import BacktestConfigV2, OverlayConfig


@dataclass(frozen=True)
class Candidate:
    """One research candidate configuration for phased search."""

    name: str
    config: BacktestConfigV2
    overlay: OverlayConfig


def build_phase45_candidates(
    *,
    base_config: BacktestConfigV2,
    base_overlay: OverlayConfig,
) -> list[Candidate]:
    """Build a compact but diverse candidate set for strict acceptance tests.

    The set intentionally mixes conservative and aggressive neighbors around the
    base setup while avoiding a combinatorial explosion.
    """
    out: list[Candidate] = []

    # Baseline anchor.
    out.append(Candidate(name="baseline", config=base_config, overlay=base_overlay))

    # Inverse blocker neighborhood.
    for ma in (20, 50, 100):
        ov = OverlayConfig(
            **{
                **base_overlay.__dict__,
                "enable_inverse_blocker": True,
                "trend_symbol": "SOXL",
                "trend_ma_days": ma,
            }
        )
        out.append(Candidate(name=f"inv_blocker_ma{ma}", config=base_config, overlay=ov))

    # Warmup sensitivity.
    for w in (60, 120, 260):
        cfg = BacktestConfigV2(**{**base_config.__dict__, "warmup_days": w})
        out.append(Candidate(name=f"warmup_{w}", config=cfg, overlay=base_overlay))

    # Persistence + hysteresis variants.
    for pdays in (2, 3):
        for band in (0.005, 0.01):
            ov = OverlayConfig(
                **{
                    **base_overlay.__dict__,
                    "enable_persistence": True,
                    "persistence_days": pdays,
                    "hysteresis_band_pct": band,
                }
            )
            out.append(Candidate(name=f"persist_{pdays}_band_{band}", config=base_config, overlay=ov))

    # Vol-target variants.
    for target in (0.25, 0.35, 0.50):
        ov = OverlayConfig(
            **{
                **base_overlay.__dict__,
                "enable_vol_target": True,
                "target_vol_ann": target,
                "vol_lookback_days": 20,
                "max_gross_exposure": 1.0,
            }
        )
        out.append(Candidate(name=f"vol_target_{target}", config=base_config, overlay=ov))

    # Loss-limiter variants.
    for stop in (0.08, 0.12, 0.20):
        ov = OverlayConfig(
            **{
                **base_overlay.__dict__,
                "enable_loss_limiter": True,
                "stop_loss_pct": stop,
                "max_holding_days": 15,
            }
        )
        out.append(Candidate(name=f"loss_stop_{stop}", config=base_config, overlay=ov))

    # Combined high-priority stacks.
    out.append(
        Candidate(
            name="inv_blocker_w60",
            config=BacktestConfigV2(**{**base_config.__dict__, "warmup_days": 60}),
            overlay=OverlayConfig(
                **{
                    **base_overlay.__dict__,
                    "enable_inverse_blocker": True,
                    "trend_symbol": "SOXL",
                    "trend_ma_days": 50,
                }
            ),
        )
    )
    out.append(
        Candidate(
            name="inv_blocker_persist",
            config=base_config,
            overlay=OverlayConfig(
                **{
                    **base_overlay.__dict__,
                    "enable_inverse_blocker": True,
                    "trend_symbol": "SOXL",
                    "trend_ma_days": 50,
                    "enable_persistence": True,
                    "persistence_days": 2,
                    "hysteresis_band_pct": 0.005,
                }
            ),
        )
    )
    out.append(
        Candidate(
            name="inv_blocker_voltarget",
            config=base_config,
            overlay=OverlayConfig(
                **{
                    **base_overlay.__dict__,
                    "enable_inverse_blocker": True,
                    "trend_symbol": "SOXL",
                    "trend_ma_days": 50,
                    "enable_vol_target": True,
                    "target_vol_ann": 0.35,
                    "vol_lookback_days": 20,
                }
            ),
        )
    )

    return out
