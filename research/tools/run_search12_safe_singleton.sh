#!/usr/bin/env bash
set -euo pipefail
ROOT="/home/chewy/projects/trading-compose-dev"
LOG="$ROOT/research/reports/meta_router_12of12/search_12of12_envelope_safe_s42.log"
LOCK="$ROOT/research/runlocks/search12_safe.lock"
cd "$ROOT"
exec 9>"$LOCK"
flock -n 9 || { echo "already_running"; exit 0; }
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONUNBUFFERED=1
exec taskset -c 0-1 nice -n 15 \
  "$ROOT/composer_original/.venv/bin/python" \
  "$ROOT/research/tools/search_12of12_envelope.py" \
  --env-file "$ROOT/.env.dev" \
  --env-override \
  --mode paper \
  --data-feed sip \
  --strategy-profile aggr_adapt_t10_tr2_rv14_b85_m8_M30_intraday_pl_5m \
  --end-date 2026-03-27 \
  --initial-equity 10000 \
  --slippage-bps 1 \
  --sell-fee-bps 0 \
  --rebalance-time-ny 15:55 \
  --runtime-profit-lock-order-type market_order \
  --workers 1 \
  --max-candidates 80 \
  --topk 20 \
  --seed 42 \
  --output-prefix search_12of12_envelope_safe_s42 \
  >> "$LOG" 2>&1
