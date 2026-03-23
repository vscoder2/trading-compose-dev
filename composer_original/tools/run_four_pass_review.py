#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
COMPOSER_ORIGINAL_DIR = ROOT / "composer_original"
REPORTS_DIR = COMPOSER_ORIGINAL_DIR / "reports"
VENV_PY = COMPOSER_ORIGINAL_DIR / ".venv" / "bin" / "python"
ROUND4_JSON = REPORTS_DIR / "implementation_round4_checks.json"
ROUND4_MD = REPORTS_DIR / "implementation_round4_checks.md"


def _python_bin() -> str:
    if VENV_PY.exists():
        return str(VENV_PY)
    return sys.executable or "python3"


def _run(cmd: list[str]) -> dict:
    p = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, check=False)
    return {
        "command": " ".join(cmd),
        "returncode": p.returncode,
        "passed": p.returncode == 0,
        "stdout": p.stdout.strip(),
        "stderr": p.stderr.strip(),
    }


def main() -> int:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    py = _python_bin()
    implementation_runner = COMPOSER_ORIGINAL_DIR / "tools" / "run_implementation_checks.py"

    passes: list[dict] = []
    for pass_idx in (1, 2, 3, 4):
        run_result = _run([py, str(implementation_runner)])

        pass_entry: dict = {
            "pass_index": pass_idx,
            "command": run_result["command"],
            "returncode": run_result["returncode"],
            "passed": run_result["passed"],
            "stdout": run_result["stdout"],
            "stderr": run_result["stderr"],
            "round4_report_json": None,
            "round4_report_md": None,
        }

        if ROUND4_JSON.exists():
            archived_json = REPORTS_DIR / f"implementation_round4_checks_pass{pass_idx}.json"
            shutil.copy2(ROUND4_JSON, archived_json)
            pass_entry["round4_report_json"] = str(archived_json)
        if ROUND4_MD.exists():
            archived_md = REPORTS_DIR / f"implementation_round4_checks_pass{pass_idx}.md"
            shutil.copy2(ROUND4_MD, archived_md)
            pass_entry["round4_report_md"] = str(archived_md)

        passes.append(pass_entry)

    summary = {
        "review_type": "four_pass_review",
        "python_bin": py,
        "pass_count": 4,
        "passed_count": sum(1 for p in passes if p["passed"]),
        "failed_count": sum(1 for p in passes if not p["passed"]),
        "passes": passes,
    }

    out_json = REPORTS_DIR / "four_pass_review_report.json"
    out_md = REPORTS_DIR / "four_pass_review_report.md"
    out_json.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    lines = [
        "# Four-Pass Review Report",
        "",
        f"- Python interpreter: `{py}`",
        f"- Pass count: {summary['pass_count']}",
        f"- Passed: {summary['passed_count']}",
        f"- Failed: {summary['failed_count']}",
        "",
    ]
    for p in passes:
        lines.append(f"## Pass {p['pass_index']} - {'PASS' if p['passed'] else 'FAIL'}")
        lines.append("")
        lines.append(f"- Command: `{p['command']}`")
        lines.append(f"- Return code: `{p['returncode']}`")
        if p["round4_report_json"]:
            lines.append(f"- Archived JSON: `{p['round4_report_json']}`")
        if p["round4_report_md"]:
            lines.append(f"- Archived MD: `{p['round4_report_md']}`")
        lines.append("")
        lines.append("### Stdout")
        lines.append("")
        lines.append("```text")
        lines.append(p["stdout"])
        lines.append("```")
        lines.append("")
        lines.append("### Stderr")
        lines.append("")
        lines.append("```text")
        lines.append(p["stderr"])
        lines.append("```")
        lines.append("")
    out_md.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["failed_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
