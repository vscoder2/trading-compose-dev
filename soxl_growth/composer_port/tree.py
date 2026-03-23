from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from soxl_growth.composer_port.nodes import Context


@dataclass
class DictContext(Context):
    closes: Mapping[str, Sequence[float]]

    def close_series(self, symbol: str) -> Sequence[float]:
        if symbol not in self.closes:
            raise KeyError(f"Missing close series for {symbol}")
        return self.closes[symbol]
