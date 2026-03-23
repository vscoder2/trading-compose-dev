# Switch Runtime V1 Streamlit UI

## What This UI Provides
- Secure login screen for dashboard access.
- Real-time style monitoring of:
  - runtime `state_kv` keys
  - runtime `events`
  - switch variant transitions
  - profit-lock and rebalance orders
  - cycle-level regime metrics and execution summaries
- Polished command-center layout with filters and runbook tab.

## Files
- `app.py`: Streamlit dashboard app.
- `generate_password_hash.py`: helper to generate password hash for login.
- `requirements.txt`: UI dependencies.

## Install
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/pip install -r \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/ui/requirements.txt
```

## Configure Login
Set at minimum:
- `SWITCH_UI_USERNAME`
- `SWITCH_UI_PASSWORD_HASH`

Generate hash:
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/python \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/ui/generate_password_hash.py
```

Example env exports:
```bash
export SWITCH_UI_USERNAME=admin
export SWITCH_UI_PASSWORD_HASH='pbkdf2_sha256$210000$<salt>$<digest>'
```

Optional local-only fallback (not recommended for production):
```bash
export SWITCH_UI_ALLOW_PLAIN_PASSWORD=1
export SWITCH_UI_PASSWORD='<your-password>'
```

## Run
```bash
/home/chewy/projects/trading-compose-dev/composer_original/.venv/bin/streamlit run \
  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/ui/app.py
```

## Runtime DB Path
The app auto-detects likely DB files and also lets you enter a custom DB path.
Default target is typically:
- `/home/chewy/projects/trading-compose-dev/switch_runtime_v1_runtime.db`

## Notes
- This UI does not submit orders and does not alter runtime logic.
- It is read-only against the selected SQLite runtime DB.
