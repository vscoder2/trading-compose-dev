#!/usr/bin/env bash
set -euo pipefail

cd /home/chewy/projects/trading-compose-dev

echo "[1/3] Phase0 validator"
improvements2_impl/tools/validate_phase0.py

echo "[2/3] Phase1 unit tests"
composer_original/.venv/bin/python -m unittest discover -s improvements2_impl/tests -v

echo "[3/3] py_compile"
composer_original/.venv/bin/python -m py_compile \
  improvements2_impl/src/*.py \
  improvements2_impl/tests/*.py \
  improvements2_impl/tools/validate_phase0.py

echo "All checks passed."
