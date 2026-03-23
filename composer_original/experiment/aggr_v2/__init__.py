"""AGGR v2 isolated research package.

This package is intentionally separate from production/runtime modules.
It provides a sandbox for strategy research, validation, and reporting
without modifying existing project code paths.
"""

from .profiles import LOCKED_PROFILES, get_profile

__all__ = ["LOCKED_PROFILES", "get_profile"]
