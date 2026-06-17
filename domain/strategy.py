"""Strategy-facing contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Mapping, Protocol

import numpy as np

from domain.market import BidAsk, SUPPORTED_INTERVALS, require_symbol
from domain.portfolio import AccountSnapshot, PortfolioTarget, Position, RiskSpec


@dataclass(frozen=True)
class DataWindowSpec:
    name: str
    interval: str
    lookback: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("window name is required")
        if self.interval not in SUPPORTED_INTERVALS:
            raise ValueError(f"unsupported interval: {self.interval}")
        if self.lookback < 1:
            raise ValueError("lookback must be >= 1")


@dataclass(frozen=True)
class StrategyDataSpec:
    windows: tuple[DataWindowSpec, ...]

    def __post_init__(self) -> None:
        if not self.windows:
            raise ValueError("at least one data window is required")
        seen: set[str] = set()
        for window in self.windows:
            if window.name in seen:
                raise ValueError(f"duplicate data window name: {window.name}")
            seen.add(window.name)


@dataclass(frozen=True)
class ScheduleSpec:
    rebalance: str

    def __post_init__(self) -> None:
        if self.rebalance not in SUPPORTED_INTERVALS:
            raise ValueError(f"unsupported rebalance interval: {self.rebalance}")


@dataclass(frozen=True)
class CapitalSpec:
    amount: float
    mode: str = "notional"

    def __post_init__(self) -> None:
        from domain.market import require_finite

        if self.mode != "notional":
            raise ValueError("only notional capital mode is supported")
        object.__setattr__(self, "amount", require_finite(self.amount, "capital amount", positive=True))


@dataclass(frozen=True)
class StrategySpec:
    name: str
    class_path: str
    universe: tuple[str, ...]
    schedule: ScheduleSpec
    data: StrategyDataSpec
    capital: CapitalSpec
    risk: RiskSpec = field(default_factory=RiskSpec)
    params: Mapping[str, Any] = field(default_factory=dict)
    allow_adoption: bool = False

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("strategy name is required")
        if not self.class_path:
            raise ValueError("strategy class is required")
        if not self.universe:
            raise ValueError("strategy universe is required")
        universe = tuple(require_symbol(s) for s in self.universe)
        seen: set[str] = set()
        for symbol in universe:
            if symbol in seen:
                raise ValueError(f"duplicate universe symbol: {symbol}")
            seen.add(symbol)
        object.__setattr__(self, "universe", universe)
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True)
class StrategyContext:
    strategy: str
    now: datetime
    session: date
    trigger: str
    universe: tuple[str, ...]
    prices: Mapping[str, float]
    bid_ask: Mapping[str, BidAsk]
    windows: Mapping[str, np.ndarray]
    account: AccountSnapshot
    positions: Mapping[str, Position]
    current_weights: Mapping[str, float]


class Strategy(Protocol):
    def evaluate(self, ctx: StrategyContext) -> PortfolioTarget: ...
