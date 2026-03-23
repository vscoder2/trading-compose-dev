#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSER_ORIGINAL_DIR = ROOT / "composer_original"
REPORTS_DIR = COMPOSER_ORIGINAL_DIR / "reports"
VENV_PY = COMPOSER_ORIGINAL_DIR / ".venv" / "bin" / "python"


def _run(cmd: list[str]) -> dict:
    p = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, check=False)
    return {
        "command": " ".join(cmd),
        "returncode": p.returncode,
        "passed": p.returncode == 0,
        "stdout": p.stdout.strip(),
        "stderr": p.stderr.strip(),
    }


def _python_bin() -> str:
    if VENV_PY.exists():
        return str(VENV_PY)
    return sys.executable or "python3"


def main() -> int:
    round_name = "implementation_round_4"
    round_title = "Implementation Round 4: Four Deep Checks (CPU+GPU)"
    report_json_path = REPORTS_DIR / "implementation_round4_checks.json"
    report_md_path = REPORTS_DIR / "implementation_round4_checks.md"

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    py = _python_bin()

    # Preflight: refresh golden snapshot before running deep checks.
    preflight = _run([py, str(COMPOSER_ORIGINAL_DIR / "tools" / "generate_backtest_golden_snapshot.py")])
    if not preflight["passed"]:
        summary = {
            "round": round_name,
            "python_bin": py,
            "preflight": preflight,
            "check_count": 0,
            "passed_count": 0,
            "failed_count": 1,
            "checks": [],
        }
        report_json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        report_md_path.write_text(
            "\n".join(
                [
                    f"# {round_title}",
                    "",
                    "Preflight failed while generating golden snapshot.",
                    "",
                    f"- Command: `{preflight['command']}`",
                    f"- Return code: `{preflight['returncode']}`",
                    "",
                    "## Stdout",
                    "```text",
                    preflight["stdout"],
                    "```",
                    "",
                    "## Stderr",
                    "```text",
                    preflight["stderr"],
                    "```",
                ]
            ),
            encoding="utf-8",
        )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 1

    checks = [
        _run([py, str(COMPOSER_ORIGINAL_DIR / "tools" / "freeze_baseline.py"), "--verify"]),
        _run(
            [
                py,
                "-m",
                "unittest",
                "-v",
                "composer_original.tests.test_phase1_hardening",
                "composer_original.tests.test_phase2_data_indicator",
                "composer_original.tests.test_phase3_backtest_parity_cli",
            ]
        ),
        _run([py, str(COMPOSER_ORIGINAL_DIR / "tools" / "run_deep_checks.py")]),
        _run(
            [
                py,
                str(COMPOSER_ORIGINAL_DIR / "tools" / "run_cpu_gpu_backtests.py"),
                "--output-json",
                str(COMPOSER_ORIGINAL_DIR / "reports" / "cpu_gpu_backtest_report.json"),
            ]
        ),
    ]
    summary = {
        "round": round_name,
        "python_bin": py,
        "preflight": preflight,
        "check_count": len(checks),
        "passed_count": sum(1 for c in checks if c["passed"]),
        "failed_count": sum(1 for c in checks if not c["passed"]),
        "checks": checks,
    }
    report_json_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        f"# {round_title}",
        "",
        f"- Python interpreter: `{py}`",
        f"- Check count: {summary['check_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Failed: {summary['failed_count']}",
        "",
    ]
    for idx, c in enumerate(checks, start=1):
        lines.append(f"## Check {idx} - {'PASS' if c['passed'] else 'FAIL'}")
        lines.append("")
        lines.append(f"- Command: `{c['command']}`")
        lines.append(f"- Return code: `{c['returncode']}`")
        lines.append("")
        lines.append("### Stdout")
        lines.append("")
        lines.append("```text")
        lines.append(c["stdout"])
        lines.append("```")
        lines.append("")
        lines.append("### Stderr")
        lines.append("")
        lines.append("```text")
        lines.append(c["stderr"])
        lines.append("```")
        lines.append("")
    report_md_path.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
