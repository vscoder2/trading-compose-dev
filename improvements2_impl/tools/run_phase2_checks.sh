#!/usr/bin/env bash
set -euo pipefail

cd /home/chewy/projects/trading-compose-dev

echo "[1/4] Phase0 validator"
improvements2_impl/tools/validate_phase0.py

echo "[2/4] Unit tests (Phase1 + Phase2)"
composer_original/.venv/bin/python -m unittest discover -s improvements2_impl/tests -v

echo "[3/4] py_compile"
composer_original/.venv/bin/python -m py_compile \
  improvements2_impl/src/*.py \
  improvements2_impl/tests/*.py \
  improvements2_impl/tools/validate_phase0.py

echo "[4/4] Migration smoke check"
composer_original/.venv/bin/python - <<'PY'
from pathlib import Path
import tempfile
from improvements2_impl.src.state_adapter import ControlPlaneStore

with tempfile.TemporaryDirectory() as d:
    db = Path(d) / "smoke.db"
    store = ControlPlaneStore(db)
    applied = store.apply_migration()
    assert applied is True
    applied2 = store.apply_migration()
    assert applied2 is False
    names = set(store.list_tables())
    required = {"locks", "decision_cycles", "drift_snapshots", "open_order_state", "risk_state", "session_state", "decision_reasons", "shadow_cycles"}
    missing = required - names
    assert not missing, missing
print("Migration smoke check passed.")
PY

echo "All Phase 2 checks passed."

