# UI UX (Copied From Tradingcomposeux, Runtime-Connected)

This folder is a local copy of `/home/chewy/projects/Tradingcomposeux` placed inside the current project:

- `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/ui_ux`

No cross-project runtime dependency is required. Runtime data is bridged from the local SQLite runtime DB into:

- `src/app/data/runtime_snapshot.json`

## Data bridge

`scripts/export_runtime_snapshot.py` reads runtime tables:

- `events`
- `state_kv`

and exports frontend-friendly fields used by the app:

- stocks
- positions
- trades
- bots
- summary
- portfolioChart

Default source DB:

- `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_data/users/demo_trader/switch_runtime_v1_runtime.db`

Override with:

- `RUNTIME_DB_PATH=/abs/path/to/runtime.db`

## Run

1. Install deps

```bash
cd /home/chewy/projects/trading-compose-dev/switch_runtime_v1/ui_ux
npm install
```

2. Start UI with continuous runtime snapshot refresh

```bash
npm run dev:runtime
```

This does:

- one snapshot export immediately
- background snapshot refresh every `10s`
- starts Vite on `0.0.0.0:8787`

Optional env vars:

- `RUNTIME_DB_PATH` (runtime sqlite db path)
- `RUNTIME_SNAPSHOT_INTERVAL` (seconds, default `10`)
- `UI_PORT` (default `8787`)

## One-shot snapshot only

```bash
npm run snapshot
```

## Notes

- Existing mock data is now overridden when runtime snapshot arrays are present.
- If runtime DB is empty, the app falls back to built-in sample data.
  
