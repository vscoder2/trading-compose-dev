"""Decision ledger helpers for deterministic auditability."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def _jsonable(value: Any) -> Any:
    """Convert supported objects into stable JSON-serializable data."""

    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda x: str(x[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def canonical_snapshot_blob(snapshot: dict[str, Any]) -> str:
    """Return canonical JSON for deterministic hashing."""

    normalized = _jsonable(snapshot)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_decision_hash(snapshot: dict[str, Any]) -> str:
    """Compute sha256 over canonical snapshot JSON."""

    blob = canonical_snapshot_blob(snapshot).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append one normalized record to a JSONL file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(canonical_snapshot_blob(record))
        f.write("\n")

