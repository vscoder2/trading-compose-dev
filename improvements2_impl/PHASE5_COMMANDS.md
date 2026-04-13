# Phase 5 Commands

Working directory:

```bash
cd /home/chewy/projects/trading-compose-dev
```

Run full Phase 5 checks:

```bash
improvements2_impl/tools/run_phase5_checks.sh
```

Run only shadow sidecar tests:

```bash
composer_original/.venv/bin/python -m unittest improvements2_impl.tests.test_phase5_shadow_sidecar -v
```

Quick shadow smoke:

```bash
composer_original/.venv/bin/python - <<'PY'
from pathlib import Path
import tempfile
from improvements2_impl.src.models import ActionIntent
from improvements2_impl.src.state_adapter import ControlPlaneStore
from improvements2_impl.src.shadow_eval import run_shadow_cycle

with tempfile.TemporaryDirectory() as td:
    db = Path(td) / "shadow.db"
    store = ControlPlaneStore(db)
    store.apply_migration(Path("improvements2_impl/migrations/001_control_plane.sql"))
    r = run_shadow_cycle(
        store=store,
        cycle_id="smoke-c1",
        variant_name="shadow_demo",
        shadow_effective_target={"SOXL": 0.5},
        shadow_actions=[ActionIntent("SOXL","buy",1.0,"rebalance_add","smoke","r")],
    )
    print(r)
PY
```
