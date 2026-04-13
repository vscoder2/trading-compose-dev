#!/usr/bin/env bash
set -euo pipefail

cd /home/chewy/projects/trading-compose-dev

echo "[1/5] Phase0 validator"
improvements2_impl/tools/validate_phase0.py

echo "[2/5] Decision-quality tests (T-021..T-023)"
composer_original/.venv/bin/python -m unittest improvements2_impl.tests.test_phase3_decision_quality -v

echo "[3/5] Full suite compatibility"
composer_original/.venv/bin/python -m unittest discover -s improvements2_impl/tests -v >/tmp/improvements2_fullsuite.log
tail -n 8 /tmp/improvements2_fullsuite.log

echo "[4/5] py_compile"
composer_original/.venv/bin/python -m py_compile \
  improvements2_impl/src/*.py \
  improvements2_impl/tests/*.py \
  improvements2_impl/tools/validate_phase0.py

echo "[5/5] Scope isolation quick check"
git status --short | sed -n '1,120p'

echo "All Phase 3 decision-quality checks passed."
