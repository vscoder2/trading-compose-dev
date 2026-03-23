from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from soxl_growth.config import ComposerConfig
from soxl_growth.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AllocationMismatch:
    trading_day: str
    symbol: str
    expected_weight: float
    actual_weight: float


class ComposerParityClient:
    """Small Composer API client for parity comparison workflows."""

    def __init__(self, config: ComposerConfig) -> None:
        self.config = config

    def fetch_backtest(self, symphony_id: str, start_date: date | None = None, end_date: date | None = None) -> dict:
        params: dict[str, str] = {}
        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.config.api_base_url}/api/v0.1/symphonies/{symphony_id}/backtest{query}"

        headers = {"Accept": "application/json"}
        if self.config.api_token:
            headers["Authorization"] = f"Bearer {self.config.api_token}"

        logger.info("Fetching Composer backtest symphony_id=%s", symphony_id)
        req = Request(url=url, headers=headers, method="GET")
        with urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)


def compare_allocations(
    oracle_daily_allocations: dict[str, dict[str, float]],
    local_daily_allocations: dict[str, dict[str, float]],
    tolerance: float = 1e-6,
) -> list[AllocationMismatch]:
    mismatches: list[AllocationMismatch] = []
    all_days = set(oracle_daily_allocations) | set(local_daily_allocations)
    for day in sorted(all_days):
        expected = oracle_daily_allocations.get(day, {})
        actual = local_daily_allocations.get(day, {})
        symbols = set(expected) | set(actual)
        for symbol in sorted(symbols):
            exp = float(expected.get(symbol, 0.0))
            act = float(actual.get(symbol, 0.0))
            if abs(exp - act) > tolerance:
                mismatches.append(
                    AllocationMismatch(
                        trading_day=day,
                        symbol=symbol,
                        expected_weight=exp,
                        actual_weight=act,
                    )
                )
    return mismatches
