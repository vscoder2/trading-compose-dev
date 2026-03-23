#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
COMPOSER_ORIGINAL_DIR = ROOT / "composer_original"
ORIGINAL_CLJ = COMPOSER_ORIGINAL_DIR / "files" / "composer_original_file.txt"
PY_TREE = ROOT / "soxl_growth" / "composer_port" / "symphony_soxl_growth_v245_rl.py"
SPEC_DIR = COMPOSER_ORIGINAL_DIR / "spec"
SNAPSHOT_PATH = SPEC_DIR / "baseline_snapshot_v245.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 64), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_clj_conditions(source: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        r"\((<=|>=)\s+\((max-drawdown|stdev-return|cumulative-return|rsi)\s+\"([A-Z]+)\"\s+\{:window\s+(\d+)\}\)\s+(-?\d+(?:\.\d+)?)\)"
    )
    out: list[dict[str, Any]] = []
    for op, metric, symbol, window, value in pattern.findall(source):
        out.append(
            {
                "metric": metric,
                "symbol": symbol,
                "window": int(window),
                "op": op,
                "value": float(value),
            }
        )
    out.sort(key=lambda x: (x["metric"], x["symbol"], x["window"], x["op"], x["value"]))
    return out


def _build_snapshot() -> dict[str, Any]:
    original_source = ORIGINAL_CLJ.read_text(encoding="utf-8")
    threshold_map = _extract_clj_conditions(original_source)
    return {
        "strategy_name": "SOXL Growth v2.4.5 RL",
        "source_files": {
            "original_clj": str(ORIGINAL_CLJ),
            "python_tree": str(PY_TREE),
        },
        "hashes": {
            "original_clj_sha256": _sha256(ORIGINAL_CLJ),
            "python_tree_sha256": _sha256(PY_TREE),
        },
        "threshold_count": len(threshold_map),
        "threshold_map": threshold_map,
    }


def _normalize_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    # Keep verification stable if absolute paths differ.
    normalized = dict(snapshot)
    normalized["source_files"] = {
        "original_clj": "composer_original/files/composer_original_file.txt",
        "python_tree": "soxl_growth/composer_port/symphony_soxl_growth_v245_rl.py",
    }
    return normalized


def main() -> int:
    parser = argparse.ArgumentParser(description="Freeze/verify baseline snapshot for composer original strategy")
    parser.add_argument("--verify", action="store_true", help="Verify current baseline against frozen snapshot")
    args = parser.parse_args()

    SPEC_DIR.mkdir(parents=True, exist_ok=True)
    current = _normalize_snapshot(_build_snapshot())

    if args.verify:
        if not SNAPSHOT_PATH.exists():
            print(json.dumps({"ok": False, "error": "snapshot_missing", "path": str(SNAPSHOT_PATH)}, indent=2))
            return 1
        frozen = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        ok = frozen == current
        print(
            json.dumps(
                {
                    "ok": ok,
                    "snapshot_path": str(SNAPSHOT_PATH),
                    "frozen_hashes": frozen.get("hashes", {}),
                    "current_hashes": current.get("hashes", {}),
                    "frozen_threshold_count": frozen.get("threshold_count"),
                    "current_threshold_count": current.get("threshold_count"),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if ok else 1

    SNAPSHOT_PATH.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": True, "snapshot_written": str(SNAPSHOT_PATH)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

