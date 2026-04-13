#!/usr/bin/env python3
"""Phase 0 canonical artifact validator.

This script validates the planning artifacts in improvements2_impl without touching
any existing runtime code. It performs three review passes:

1. Structural checks: files exist, CSV columns are valid, IDs are unique.
2. Consistency checks: dependencies resolve, acceptance coverage is complete.
3. Terminology checks: banned legacy terms are absent from canonical docs.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]

BACKLOG_CSV = ROOT / "BACKLOG_CANONICAL.csv"
ACCEPT_CSV = ROOT / "ACCEPTANCE_TEST_MATRIX.csv"
SPEC_MD = ROOT / "SPEC_CANONICAL.md"
REVIEW_MD = ROOT / "REVIEW_3PASS.md"
REPORT_MD = ROOT / "reports" / "phase0_review_report.md"

REQUIRED_BACKLOG_COLS = {
    "id",
    "title",
    "phase",
    "priority",
    "bucket",
    "difficulty",
    "depends_on",
    "status",
    "runtime_insertion_point",
    "new_tables_or_keys",
    "notes",
}

REQUIRED_ACCEPT_COLS = {
    "item_id",
    "test_id",
    "test_name",
    "level",
    "pass_criteria",
}

# Terms we intentionally disallow in canonical docs due to known mismatch.
BANNED_TERMS = [
    "parity_executed_day",  # incorrect key for switch runtime
]


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _validate_columns(rows: list[dict[str, str]], required: set[str], label: str) -> CheckResult:
    if not rows:
        return CheckResult(label, False, "CSV has no rows")
    cols = set(rows[0].keys())
    missing = sorted(required - cols)
    if missing:
        return CheckResult(label, False, f"missing columns: {missing}")
    return CheckResult(label, True, "required columns present")


def _validate_unique_ids(rows: Iterable[dict[str, str]], key: str, label: str) -> CheckResult:
    seen: set[str] = set()
    dupes: set[str] = set()
    for row in rows:
        v = str(row.get(key, "")).strip()
        if not v:
            dupes.add("<blank>")
            continue
        if v in seen:
            dupes.add(v)
        seen.add(v)
    if dupes:
        return CheckResult(label, False, f"duplicate/invalid {key}: {sorted(dupes)}")
    return CheckResult(label, True, f"{len(seen)} unique {key} values")


def _validate_dependencies(backlog_rows: list[dict[str, str]]) -> CheckResult:
    ids = {r["id"].strip() for r in backlog_rows if r.get("id")}
    unresolved: list[str] = []
    for r in backlog_rows:
        item = r["id"].strip()
        deps_raw = (r.get("depends_on") or "").strip()
        if not deps_raw:
            continue
        for dep in [d.strip() for d in deps_raw.split("|") if d.strip()]:
            if dep not in ids:
                unresolved.append(f"{item}->{dep}")
    if unresolved:
        return CheckResult("dependency resolution", False, f"unresolved: {unresolved}")
    return CheckResult("dependency resolution", True, "all dependencies resolved")


def _validate_acceptance_coverage(backlog_rows: list[dict[str, str]], accept_rows: list[dict[str, str]]) -> CheckResult:
    planned_ids = {r["id"].strip() for r in backlog_rows if r.get("status", "").strip() == "planned"}
    test_ids = {r["item_id"].strip() for r in accept_rows if r.get("item_id")}
    missing = sorted(planned_ids - test_ids)
    if missing:
        return CheckResult("acceptance coverage", False, f"missing test rows for: {missing}")
    return CheckResult("acceptance coverage", True, "all planned items have acceptance coverage")


def _validate_banned_terms(paths: list[Path]) -> CheckResult:
    hits: list[str] = []
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="ignore")
        for term in BANNED_TERMS:
            if term in text:
                hits.append(f"{p.name}:{term}")
    if hits:
        return CheckResult("terminology alignment", False, f"banned terms found: {hits}")
    return CheckResult("terminology alignment", True, "no banned terms in canonical docs")


def _write_report(results: list[CheckResult]) -> None:
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    lines = [
        "# Phase 0 Review Report",
        "",
        f"Summary: {passed}/{total} checks passed.",
        "",
        "| Check | Status | Details |",
        "|---|---|---|",
    ]
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        lines.append(f"| {r.name} | {status} | {r.details} |")
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    results: list[CheckResult] = []

    required_files = [BACKLOG_CSV, ACCEPT_CSV, SPEC_MD, REVIEW_MD]
    missing = [str(p) for p in required_files if not p.exists()]
    if missing:
        results.append(CheckResult("required files", False, f"missing: {missing}"))
        _write_report(results)
        print("Validation failed: missing required files")
        return 1

    results.append(CheckResult("required files", True, "all required files present"))

    backlog_rows = _read_csv(BACKLOG_CSV)
    accept_rows = _read_csv(ACCEPT_CSV)

    # Pass 1: structural checks.
    results.append(_validate_columns(backlog_rows, REQUIRED_BACKLOG_COLS, "backlog columns"))
    results.append(_validate_columns(accept_rows, REQUIRED_ACCEPT_COLS, "acceptance columns"))
    results.append(_validate_unique_ids(backlog_rows, "id", "backlog IDs"))
    results.append(_validate_unique_ids(accept_rows, "test_id", "acceptance test IDs"))

    # Pass 2: consistency checks.
    results.append(_validate_dependencies(backlog_rows))
    results.append(_validate_acceptance_coverage(backlog_rows, accept_rows))

    # Pass 3: terminology checks.
    results.append(_validate_banned_terms([SPEC_MD, REVIEW_MD]))

    _write_report(results)

    any_fail = any(not r.passed for r in results)
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.name}: {r.details}")

    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
