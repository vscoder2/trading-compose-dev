#!/usr/bin/env python3
"""GPU environment diagnostics for research GPU-native batch engine."""

from __future__ import annotations

import os
import subprocess
import sys


def _run(cmd: list[str]) -> tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def main() -> int:
    print("[gpu-check] python:", sys.executable)
    print("[gpu-check] CUDA_PATH:", os.environ.get("CUDA_PATH", ""))
    print("[gpu-check] LD_LIBRARY_PATH:", os.environ.get("LD_LIBRARY_PATH", ""))

    code, out, err = _run(["nvidia-smi", "--query-gpu=name,index,driver_version", "--format=csv,noheader"])
    if code == 0:
        print("[gpu-check] nvidia-smi:")
        print(out)
    else:
        print("[gpu-check] nvidia-smi unavailable:", err)

    try:
        import cupy as cp  # type: ignore

        print("[gpu-check] cupy version:", cp.__version__)
        print("[gpu-check] cupy device count:", cp.cuda.runtime.getDeviceCount())
        try:
            a = cp.full((16,), 2.0, dtype=cp.float64)
            b = cp.full((16,), 3.0, dtype=cp.float64)
            s = float((a + b).sum())
            print("[gpu-check] cupy kernel test OK sum=", s)
            return 0
        except Exception as exc:
            print("[gpu-check] cupy kernel test FAILED:", exc)
            print("[gpu-check] hint: set CUDA_PATH and ensure NVRTC/toolkit is available for CuPy")
            return 2
    except Exception as exc:
        print("[gpu-check] cupy import FAILED:", exc)
        print("[gpu-check] hint: install cupy-cuda12x in this venv")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
