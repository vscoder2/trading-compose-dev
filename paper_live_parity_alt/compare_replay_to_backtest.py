#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _num(obj, *keys, default=0.0) -> float:
    cur = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    try:
        return float(cur)
    except Exception:
        return default


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Compare fill-replay summary against backtest summary.")
    p.add_argument("--replay-summary", required=True)
    p.add_argument("--backtest-summary", required=True)
    return p


def main() -> int:
    args = _build_parser().parse_args()
    replay = _load(args.replay_summary)
    backtest = _load(args.backtest_summary)

    replay_final = _num(replay, "final_equity")
    replay_ret = _num(replay, "total_return_pct")

    # Supports either flat summary or {cpu:{...}}.
    backtest_final = _num(backtest, "final_equity", default=None)
    backtest_ret = _num(backtest, "total_return_pct", default=None)
    if backtest_final is None:
        backtest_final = _num(backtest, "cpu", "final_equity")
    if backtest_ret is None:
        backtest_ret = _num(backtest, "cpu", "total_return_pct")

    eq_diff = replay_final - backtest_final
    ret_diff = replay_ret - backtest_ret

    print(
        json.dumps(
            {
                "replay_summary": args.replay_summary,
                "backtest_summary": args.backtest_summary,
                "replay_final_equity": replay_final,
                "backtest_final_equity": backtest_final,
                "equity_diff": eq_diff,
                "replay_total_return_pct": replay_ret,
                "backtest_total_return_pct": backtest_ret,
                "return_diff_pct_points": ret_diff,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

