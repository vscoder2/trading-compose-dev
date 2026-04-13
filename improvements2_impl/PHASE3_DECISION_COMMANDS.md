# Phase 3 Decision-Quality Commands (`I2-021..I2-023`)

Working directory:

```bash
cd /home/chewy/projects/trading-compose-dev
```

Run dedicated checks:

```bash
improvements2_impl/tools/run_phase3_decision_checks.sh
```

Run only decision-quality tests:

```bash
composer_original/.venv/bin/python -m unittest improvements2_impl.tests.test_phase3_decision_quality -v
```

Quick smoke:

```bash
composer_original/.venv/bin/python - <<'PY'
from improvements2_impl.src.regime_policy import (
    HysteresisState, step_hysteresis_state, compute_regime_confidence,
    ConfidenceInputs, compute_adaptive_rebalance_threshold,
)
st = HysteresisState(regime="risk_off", enter_streak=0, exit_streak=0)
st = step_hysteresis_state(prior=st, signal=0.70)
st = step_hysteresis_state(prior=st, signal=0.71)
score, _ = compute_regime_confidence(ConfidenceInputs(0.4, 1.0, 2.0))
thr = compute_adaptive_rebalance_threshold(base_threshold_pct=0.05, realized_vol_ann=1.0, chop_score=2.0, confidence_score=score)
print(st, score, thr)
PY
```
