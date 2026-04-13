# Meta Router V2 (Isolated Research)

This folder contains a standalone second-generation meta-router research path.
No existing runtime files are modified.

## Files
- `tools/historical_meta_router_v2_windows.py`:
  - Builds `v1`, `v2`, `m0106` daily target maps.
  - Builds a `meta_router_v2` daily engine selection using rolling score + regime bonuses + hysteresis.
  - Replays minute bars and compares outcomes vs baseline engines.
- `tools/sweep_meta_router_v2_params.py`:
  - Parallel sweep of V2 router parameters.
  - Coarse ranking + full-window rerank.

## Notes
- Simulation uses the same historical replay path as existing harnesses.
- CPU/GPU columns are reported with deterministic parity simulation.
