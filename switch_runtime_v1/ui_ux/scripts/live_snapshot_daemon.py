#!/usr/bin/env python3
"""Continuously refresh runtime_snapshot.json from runtime sqlite DB."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from export_runtime_snapshot import DEFAULT_DB, DEFAULT_OUT, export_snapshot


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refresh runtime snapshot on an interval")
    p.add_argument("--db", default=str(DEFAULT_DB), help="Path to runtime sqlite db")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON path")
    p.add_argument("--interval", type=float, default=10.0, help="Refresh interval in seconds")
    p.add_argument("--trade-limit", type=int, default=300, help="Max trades to include")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    db = Path(args.db).expanduser().resolve()
    out = Path(args.out).expanduser().resolve()
    interval = max(2.0, float(args.interval))
    trade_limit = max(20, int(args.trade_limit))

    print(
        f"[snapshot-daemon] start db={db} out={out} interval={interval}s trade_limit={trade_limit}",
        flush=True,
    )
    while True:
        try:
            snap = export_snapshot(db, out, trade_limit=trade_limit)
            print(
                f"[snapshot-daemon] updated stocks={len(snap.get('stocks', []))} "
                f"positions={len(snap.get('positions', []))} trades={len(snap.get('trades', []))}",
                flush=True,
            )
        except Exception as exc:
            print(f"[snapshot-daemon] warning: {exc}", flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())

