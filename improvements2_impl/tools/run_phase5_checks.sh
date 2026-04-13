#!/usr/bin/env bash
set -euo pipefail

cd /home/chewy/projects/trading-compose-dev

echo "[1/7] Phase0 validator"
improvements2_impl/tools/validate_phase0.py

echo "[2/7] Unit tests (Phase1..Phase5)"
composer_original/.venv/bin/python -m unittest discover -s improvements2_impl/tests -v

echo "[3/7] py_compile"
composer_original/.venv/bin/python -m py_compile \
  improvements2_impl/src/*.py \
  improvements2_impl/tests/*.py \
  improvements2_impl/tools/validate_phase0.py

echo "[4/7] Migration smoke (001 + 002 + shadow table presence)"
composer_original/.venv/bin/python - <<'PY'
from pathlib import Path
import tempfile
from improvements2_impl.src.state_adapter import ControlPlaneStore

with tempfile.TemporaryDirectory() as d:
    db = Path(d) / "phase5_smoke.db"
    store = ControlPlaneStore(db)
    assert store.apply_migration(Path("improvements2_impl/migrations/001_control_plane.sql")) is True
    assert store.apply_migration(Path("improvements2_impl/migrations/002_execution_observability.sql")) is True
    names = set(store.list_tables())
    required = {"shadow_cycles", "eod_reports", "turnover_monitor_daily"}
    missing = required - names
    assert not missing, missing
print("Migration smoke passed.")
PY

echo "[5/7] Shadow no-submit smoke"
composer_original/.venv/bin/python - <<'PY'
from pathlib import Path
import tempfile
from improvements2_impl.src.models import ActionIntent
from improvements2_impl.src.state_adapter import ControlPlaneStore
from improvements2_impl.src.shadow_eval import run_shadow_cycle

with tempfile.TemporaryDirectory() as d:
    db = Path(d) / "shadow.db"
    store = ControlPlaneStore(db)
    store.apply_migration(Path("improvements2_impl/migrations/001_control_plane.sql"))
    out = run_shadow_cycle(
        store=store,
        cycle_id="smoke-shadow",
        variant_name="shadow_alt",
        shadow_effective_target={"SOXL": 0.7},
        shadow_actions=[ActionIntent("SOXL","buy",2.0,"rebalance_add","smoke","r")],
    )
    assert out.submitted_order_count == 0
    assert store.count_rows("shadow_cycles") == 1
    assert store.count_rows("open_order_state") == 0
print("Shadow no-submit smoke passed.")
PY

echo "[6/7] Full Phase4+Phase5 check compatibility"
improvements2_impl/tools/run_phase4_checks.sh >/tmp/improvements2_phase4_compat.log
tail -n 5 /tmp/improvements2_phase4_compat.log

echo "[7/7] Scope isolation quick check"
git status --short | sed -n '1,120p'

echo "All Phase 5 checks passed."
