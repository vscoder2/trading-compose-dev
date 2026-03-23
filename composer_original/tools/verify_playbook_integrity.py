#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "composer_original" / "docs" / "AGGR_INTRADAY_PL5M_PAPER_LIVE_PARITY_PLAYBOOK.md"


def _fail(msg: str) -> None:
    print(f"[FAIL] {msg}")
    raise SystemExit(1)


def _ok(msg: str) -> None:
    print(f"[OK] {msg}")


def _extract_sections(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(\d+)\)\s+(.*)$", line.strip())
        if m:
            out.append((int(m.group(1)), m.group(2).strip()))
    return out


def main() -> int:
    if not DOC.exists():
        _fail(f"playbook not found: {DOC}")

    text = DOC.read_text(encoding="utf-8", errors="replace")
    sections = _extract_sections(text)
    if not sections:
        _fail("no numbered level-2 sections found")

    nums = [n for n, _ in sections]
    expected = list(range(1, 80))
    if nums != expected:
        _fail(
            "section numbering mismatch.\n"
            f"found:    {nums[:8]} ... {nums[-8:]}\n"
            f"expected: {expected[:8]} ... {expected[-8:]}"
        )
    _ok("section numbering is contiguous (1..79)")

    required_headers = [
        "Scope and Purpose",
        "Deep Inner Workings: `runtime_backtest_parity_loop.py`",
        "Deep Inner Workings: `intraday_profit_lock_verification.py`",
        "Base Strategy Deep Dive: `SOXL Growth v2.4.5 RL`",
        "Backtest Results: This Guide Configuration (Point-in-Time Snapshot)",
        "Paper-to-Live Cutover Checklist",
        "Release Checklist Template",
        "Guide Maintenance Notes",
    ]
    names = {name for _, name in sections}
    missing_headers = [h for h in required_headers if h not in names]
    if missing_headers:
        _fail(f"missing required section headers: {missing_headers}")
    _ok("required section headers present")

    required_paths = [
        ROOT / "composer_original" / "tools" / "runtime_backtest_parity_loop.py",
        ROOT / "composer_original" / "tools" / "intraday_profit_lock_verification.py",
        ROOT / "composer_original" / "files" / "composer_original_file.txt",
        ROOT / "soxl_growth" / "composer_port" / "symphony_soxl_growth_v245_rl.py",
        ROOT
        / "composer_original"
        / "reports"
        / "batch_intraday_pl5m_paper_live_opt_cpu_gpu_1m_2m_3m_4m_5m_6m_9m_1y_2y_3y_4y_5y_7y.csv",
        ROOT
        / "composer_original"
        / "reports"
        / "batch_intraday_pl5m_paper_live_opt_cpu_gpu_1m_2m_3m_4m_5m_6m_9m_1y_2y_3y_4y_5y_7y.json",
    ]
    missing_paths = [str(p) for p in required_paths if not p.exists()]
    if missing_paths:
        _fail("missing required files:\n" + "\n".join(missing_paths))
    _ok("critical source/report files exist")

    _ok("playbook integrity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

