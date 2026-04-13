#!/usr/bin/env bash
set -euo pipefail

cd /home/chewy/projects/trading-compose-dev

echo "[1/5] Phase0 validator"
improvements2_impl/tools/validate_phase0.py

echo "[2/5] Unit tests (Phase1 + Phase2 + Phase3)"
composer_original/.venv/bin/python -m unittest discover -s improvements2_impl/tests -v

echo "[3/5] py_compile"
composer_original/.venv/bin/python -m py_compile \
  improvements2_impl/src/*.py \
  improvements2_impl/tests/*.py \
  improvements2_impl/tools/validate_phase0.py

echo "[4/5] Risk control scenario smoke check"
composer_original/.venv/bin/python - <<'PY'
from improvements2_impl.src.risk_controls import (
    ExposureInputs,
    compute_exposure_scalar,
    next_drawdown_brake_state,
    next_session_breaker_state,
    start_recovery_probe,
    step_recovery_probe,
)

s1 = compute_exposure_scalar(ExposureInputs(drawdown_pct=2.0, realized_vol_ann=0.8, chop_score=1.0))
s2 = compute_exposure_scalar(ExposureInputs(drawdown_pct=25.0, realized_vol_ann=1.8, chop_score=8.0))
assert 0.0 <= s1 <= 1.0
assert 0.0 <= s2 <= 1.0
assert s1 > s2

dd = next_drawdown_brake_state(prior_state="none", drawdown_pct=21.0)
assert dd.state == "hard_brake" and dd.blocks_adds is True

sb = next_session_breaker_state(prior_state="open", session_pnl_pct=-3.2)
assert sb.state == "adds_blocked" and sb.blocks_adds is True

probe = start_recovery_probe()
probe = step_recovery_probe(probe, hard_brake_active=False, success_signal=True)
probe = step_recovery_probe(probe, hard_brake_active=False, success_signal=True)
assert probe.level_index >= 1
print("Risk control smoke check passed.")
PY

echo "[5/5] Scope isolation quick check"
git status --short | sed -n '1,60p'

echo "All Phase 3 checks passed."
