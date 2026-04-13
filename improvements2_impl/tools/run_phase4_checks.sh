#!/usr/bin/env bash
set -euo pipefail

cd /home/chewy/projects/trading-compose-dev

echo "[1/6] Phase0 validator"
improvements2_impl/tools/validate_phase0.py

echo "[2/6] Unit tests (Phase1 + Phase2 + Phase3 + Phase4)"
composer_original/.venv/bin/python -m unittest discover -s improvements2_impl/tests -v

echo "[3/6] py_compile"
composer_original/.venv/bin/python -m py_compile \
  improvements2_impl/src/*.py \
  improvements2_impl/tests/*.py \
  improvements2_impl/tools/validate_phase0.py

echo "[4/6] Migration smoke checks (001 + 002)"
composer_original/.venv/bin/python - <<'PY'
from pathlib import Path
import tempfile
from improvements2_impl.src.state_adapter import ControlPlaneStore

with tempfile.TemporaryDirectory() as d:
    db = Path(d) / "phase4_smoke.db"
    store = ControlPlaneStore(db)
    m1 = Path("improvements2_impl/migrations/001_control_plane.sql")
    m2 = Path("improvements2_impl/migrations/002_execution_observability.sql")
    assert store.apply_migration(m1) is True
    assert store.apply_migration(m1) is False
    assert store.apply_migration(m2) is True
    assert store.apply_migration(m2) is False
    names = set(store.list_tables())
    required = {
        "eod_reports",
        "turnover_monitor_daily",
        "decision_cycles",
        "locks",
    }
    missing = required - names
    assert not missing, missing
print("Migration smoke checks passed.")
PY

echo "[5/6] EOD upsert uniqueness smoke"
composer_original/.venv/bin/python - <<'PY'
from pathlib import Path
import tempfile
from improvements2_impl.src.audit_export import build_eod_row, upsert_eod_report, list_eod_reports

with tempfile.TemporaryDirectory() as d:
    db = Path(d) / "eod.db"
    row = build_eod_row(
        report_date="2026-03-28",
        profile="smoke",
        start_equity=10000.0,
        end_equity=10200.0,
        max_drawdown_pct=2.0,
        trade_count=4,
        turnover_buy_notional=9000.0,
        turnover_sell_notional=8500.0,
    )
    upsert_eod_report(db, row)
    upsert_eod_report(db, row)
    rows = list_eod_reports(db, profile="smoke")
    assert len(rows) == 1
print("EOD upsert uniqueness check passed.")
PY

echo "[6/6] Scope isolation quick check"
git status --short | sed -n '1,80p'

echo "All Phase 4 checks passed."
