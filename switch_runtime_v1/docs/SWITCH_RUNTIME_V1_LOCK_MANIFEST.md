# Switch Runtime V1 Lock Manifest

## Purpose
This manifest locks the standalone implementation under `switch_runtime_v1` so future edits can be validated against a known-good artifact.

## Locked Artifact
- File: `/home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py`
- SHA256: `8749f4d256702cb6733b76b066685d58d123b965d14d0e86595bc02551319626`
- Lock Date (NY): `2026-03-22`

## Verification Command
```bash
sha256sum /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py
```

Expected output:
```text
8749f4d256702cb6733b76b066685d58d123b965d14d0e86595bc02551319626  /home/chewy/projects/trading-compose-dev/switch_runtime_v1/runtime_switch_loop.py
```

## Change Control Rule
- Do not modify `runtime_switch_loop.py` unless an explicit request is provided.
- If modified, update this manifest with:
  - new SHA256
  - reason for change
  - date
  - reviewer
