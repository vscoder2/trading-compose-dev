from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Sequence

from soxl_growth.portfolio.selector import select_assets
from soxl_growth.types import LeafPick


class Context(Protocol):
    def close_series(self, symbol: str) -> Sequence[float]:
        ...


@dataclass(frozen=True)
class Node:
    def eval(self, ctx: Context) -> list[LeafPick]:
        raise NotImplementedError


@dataclass(frozen=True)
class AssetNode(Node):
    symbol: str

    def eval(self, ctx: Context) -> list[LeafPick]:
        return [(self.symbol, 1.0)]


@dataclass(frozen=True)
class IfElseNode(Node):
    cond: Callable[[Context], bool]
    then_branch: Node
    else_branch: Node

    def eval(self, ctx: Context) -> list[LeafPick]:
        if self.cond(ctx):
            return self.then_branch.eval(ctx)
        return self.else_branch.eval(ctx)


@dataclass(frozen=True)
class WeightEqualNode(Node):
    children: Sequence[Node]

    def eval(self, ctx: Context) -> list[LeafPick]:
        if not self.children:
            return []
        scale = 1.0 / len(self.children)
        out: list[LeafPick] = []
        for child in self.children:
            for symbol, weight in child.eval(ctx):
                out.append((symbol, weight * scale))
        return out


@dataclass(frozen=True)
class FilterSelectNode(Node):
    metric: Callable[[str, Context], float]
    mode: str
    k: int
    assets: Sequence[str]

    def eval(self, ctx: Context) -> list[LeafPick]:
        return select_assets(
            assets=list(self.assets),
            metric=lambda symbol: self.metric(symbol, ctx),
            k=self.k,
            mode=self.mode,
        )
