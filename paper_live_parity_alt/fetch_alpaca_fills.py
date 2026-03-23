#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List

import requests


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def _load_env(env_file: str | None, override: bool) -> None:
    if not env_file:
        return
    vals = _parse_env_file(Path(env_file))
    for k, v in vals.items():
        if override or os.getenv(k) is None:
            os.environ[k] = v


def _base_url(mode: str) -> str:
    if mode == "paper":
        return "https://paper-api.alpaca.markets"
    return "https://api.alpaca.markets"


def _headers() -> Dict[str, str]:
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("ALPACA_API_SECRET")
    if not key or not secret:
        raise RuntimeError("Missing ALPACA_API_KEY and/or ALPACA_SECRET_KEY in environment.")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _fetch_fills(mode: str, after: str, until: str, page_size: int = 100) -> List[Dict]:
    url = f"{_base_url(mode)}/v2/account/activities/FILL"
    headers = _headers()
    all_rows: List[Dict] = []
    page_token = None
    while True:
        params = {"after": after, "until": until, "direction": "asc", "page_size": page_size}
        if page_token:
            params["page_token"] = page_token
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            break
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        page_token = rows[-1].get("id")
        if not page_token:
            break
    return all_rows


def _write_csv(path: Path, rows: Iterable[Dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "id",
        "transaction_time",
        "symbol",
        "side",
        "qty",
        "price",
        "net_amount",
        "per_share_amount",
        "order_id",
        "type",
        "source",
    ]
    count = 0
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            out = {c: r.get(c) for c in cols}
            w.writerow(out)
            count += 1
    return count


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fetch Alpaca FILL activities to CSV.")
    p.add_argument("--env-file", default=None)
    p.add_argument("--env-override", action="store_true")
    p.add_argument("--mode", choices=["paper", "live"], default="paper")
    p.add_argument("--after", required=True, help="YYYY-MM-DD")
    p.add_argument("--until", required=True, help="YYYY-MM-DD")
    p.add_argument("--out-csv", required=True)
    return p


def main() -> int:
    args = _build_parser().parse_args()
    _load_env(args.env_file, args.env_override)
    rows = _fetch_fills(mode=args.mode, after=args.after, until=args.until)
    out = Path(args.out_csv)
    n = _write_csv(out, rows)
    print(
        json.dumps(
            {
                "mode": args.mode,
                "after": args.after,
                "until": args.until,
                "rows": n,
                "out_csv": str(out),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

