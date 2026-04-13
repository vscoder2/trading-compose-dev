# Phase 1 Commands

All commands run from repo root:

```bash
cd /home/chewy/projects/trading-compose-dev
```

## 1) Run canonical Phase 0 validator

```bash
improvements2_impl/tools/validate_phase0.py
```

## 2) Run Phase 1 unit tests (portable, stdlib)

```bash
composer_original/.venv/bin/python -m unittest discover -s improvements2_impl/tests -v
```

## 3) Compile-check Phase 1 Python modules

```bash
composer_original/.venv/bin/python -m py_compile \
  improvements2_impl/src/*.py \
  improvements2_impl/tests/*.py \
  improvements2_impl/tools/validate_phase0.py
```

## 4) One-shot full local check

```bash
improvements2_impl/tools/run_phase1_checks.sh
```
