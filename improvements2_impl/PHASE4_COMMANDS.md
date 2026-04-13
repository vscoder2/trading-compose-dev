# Phase 4 Commands

Working directory:

```bash
cd /home/chewy/projects/trading-compose-dev
```

Run full Phase 4 checks:

```bash
improvements2_impl/tools/run_phase4_checks.sh
```

Run only Phase 4 tests:

```bash
composer_original/.venv/bin/python -m unittest improvements2_impl.tests.test_phase4_execution_observability -v
```

Quick smoke (execution policy + turnover):

```bash
composer_original/.venv/bin/python - <<'PY'
from improvements2_impl.src.execution_policy import estimate_turnover_notional
from improvements2_impl.src.models import ActionIntent
intents = [ActionIntent("SOXL","buy",2.0,"rebalance_add","smoke","r")]
print(estimate_turnover_notional(intents, {"SOXL": 50.0}))
PY
```
