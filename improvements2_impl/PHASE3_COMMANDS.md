# Phase 3 Commands

Working directory:

```bash
cd /home/chewy/projects/trading-compose-dev
```

Run all Phase 3 checks:

```bash
improvements2_impl/tools/run_phase3_checks.sh
```

Run only Phase 3 tests:

```bash
composer_original/.venv/bin/python -m unittest improvements2_impl.tests.test_phase3_risk_controls -v
```

Quick import sanity:

```bash
composer_original/.venv/bin/python - <<'PY'
from improvements2_impl.src import compute_exposure_scalar, ExposureInputs
print(compute_exposure_scalar(ExposureInputs(drawdown_pct=10, realized_vol_ann=1.1, chop_score=3)))
PY
```
