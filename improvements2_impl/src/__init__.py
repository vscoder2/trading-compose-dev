"""Phase 1 control-kernel package (separate implementation track).

This package is intentionally isolated under improvements2_impl so we can
iterate on supervisory control logic without touching existing runtime code.
"""

from .models import (
    ActionIntent,
    DecisionContext,
    DecisionResult,
    DriftRecord,
    LockState,
    OpenOrder,
    PositionState,
)
from .state_adapter import ControlPlaneStore
from .execution_policy import estimate_turnover_notional, resolve_order_conflicts
from .audit_export import EODReportRow, build_eod_row, list_eod_reports, row_to_dict, upsert_eod_report
from .shadow_eval import ShadowCycleResult, build_shadow_diff, run_shadow_cycle
from .regime_policy import (
    ConfidenceInputs,
    HysteresisConfig,
    HysteresisState,
    build_confidence_log_payload,
    compute_adaptive_rebalance_threshold,
    compute_regime_confidence,
    step_hysteresis_state,
)
from .risk_controls import (
    DrawdownBrakeResult,
    ExposureInputs,
    RecoveryProbeState,
    SessionBreakerResult,
    compute_exposure_scalar,
    next_drawdown_brake_state,
    next_session_breaker_state,
    start_recovery_probe,
    step_recovery_probe,
)

__all__ = [
    "ActionIntent",
    "ControlPlaneStore",
    "DecisionContext",
    "DecisionResult",
    "DriftRecord",
    "LockState",
    "OpenOrder",
    "PositionState",
    "DrawdownBrakeResult",
    "ExposureInputs",
    "RecoveryProbeState",
    "SessionBreakerResult",
    "compute_exposure_scalar",
    "next_drawdown_brake_state",
    "next_session_breaker_state",
    "start_recovery_probe",
    "step_recovery_probe",
    "estimate_turnover_notional",
    "resolve_order_conflicts",
    "EODReportRow",
    "build_eod_row",
    "list_eod_reports",
    "row_to_dict",
    "upsert_eod_report",
    "ShadowCycleResult",
    "build_shadow_diff",
    "run_shadow_cycle",
    "ConfidenceInputs",
    "HysteresisConfig",
    "HysteresisState",
    "build_confidence_log_payload",
    "compute_adaptive_rebalance_threshold",
    "compute_regime_confidence",
    "step_hysteresis_state",
]
