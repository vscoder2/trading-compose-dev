#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG = ROOT / "configs"
OUT_MD = ROOT / "GRID_CATALOG.md"
OUT_CSV = ROOT / "GRID_CATALOG.csv"

rows = []
for p in sorted(CFG.glob("*.json")):
    data = json.loads(p.read_text())
    keys = list(data.keys())
    counts = []
    for k in keys:
        v = data[k]
        if isinstance(v, list):
            counts.append(len(v))
        else:
            counts.append(1)
    combos = 1
    for c in counts:
        combos *= c
    rows.append({
        "file": p.name,
        "combos": combos,
        "dimensions": len(keys),
        "keys": ", ".join(keys),
    })

rows.sort(key=lambda r: r["combos"])

# csv
lines = ["file,combos,dimensions,keys"]
for r in rows:
    lines.append(f"{r['file']},{r['combos']},{r['dimensions']},\"{r['keys']}\"")
OUT_CSV.write_text("\n".join(lines) + "\n")

# md
md = ["# Grid Catalog", "", "| Grid | Combos | Dimensions |", "|---|---:|---:|"]
for r in rows:
    md.append(f"| `{r['file']}` | {r['combos']:,} | {r['dimensions']} |")
OUT_MD.write_text("\n".join(md) + "\n")

print(f"wrote: {OUT_MD}")
print(f"wrote: {OUT_CSV}")
