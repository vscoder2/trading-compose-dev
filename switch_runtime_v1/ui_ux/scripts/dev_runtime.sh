#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DB_PATH="${RUNTIME_DB_PATH:-/home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_data/users/demo_trader/switch_runtime_v1_runtime.db}"
OUT_PATH="${RUNTIME_SNAPSHOT_OUT:-$ROOT_DIR/src/app/data/runtime_snapshot.json}"
INTERVAL="${RUNTIME_SNAPSHOT_INTERVAL:-10}"
PORT="${UI_PORT:-8787}"

python3 "$ROOT_DIR/scripts/export_runtime_snapshot.py" --db "$DB_PATH" --out "$OUT_PATH" --trade-limit 300
python3 "$ROOT_DIR/scripts/live_snapshot_daemon.py" --db "$DB_PATH" --out "$OUT_PATH" --interval "$INTERVAL" --trade-limit 300 &
DAEMON_PID=$!

cleanup() {
  kill "$DAEMON_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

echo "[ui_ux] starting Vite on 0.0.0.0:$PORT with runtime snapshot daemon"
if [ -x "$ROOT_DIR/node_modules/.bin/vite" ]; then
  exec "$ROOT_DIR/node_modules/.bin/vite" --host 0.0.0.0 --port "$PORT"
fi
exec npx vite --host 0.0.0.0 --port "$PORT"
