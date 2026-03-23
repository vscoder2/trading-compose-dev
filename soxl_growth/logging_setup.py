from __future__ import annotations

import logging
from logging import Logger


def configure_logging(level: str = "INFO") -> None:
    """Configure global logging for CLI/runtime use.

    This function is idempotent and intentionally simple so it works in both
    local scripts and containerized deployments.
    """
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level.upper())
        return

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root.setLevel(level.upper())
    root.addHandler(handler)


def get_logger(name: str) -> Logger:
    return logging.getLogger(name)
