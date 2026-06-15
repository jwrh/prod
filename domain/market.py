"""Market-data domain types."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Sequence

import numpy as np


SUPPORTED_INTERVALS = frozenset({"1m", "5m", "15m", "1h", "1d"})
INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "1d": 86_400}


def require_symbol(symbol: str) -> str:
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    return symbol.strip().upper()


def require_finite(value: float, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be finite")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if positive and number <= 0:
        raise ValueError(f"{name} must be > 0")
    return number


@dataclass(frozen=True)
class BidAsk:
    bid: float
    ask: float

    def __post_init__(self) -> None:
        bid = require_finite(self.bid, "bid", positive=True)
        ask = require_finite(self.ask, "ask", positive=True)
        if bid >= ask:
            raise ValueError("bid < ask is required")
        object.__setattr__(self, "bid", bid)
        object.__setattr__(self, "ask", ask)


@dataclass(frozen=True)
class Quote:
    symbol: str
    price: float
    now: datetime | None = None
    bid: float | None = None
    ask: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", require_symbol(self.symbol))
        object.__setattr__(self, "price", require_finite(self.price, "price", positive=True))
        if self.now is None:
            object.__setattr__(self, "now", datetime.now(timezone.utc))
        if self.bid is not None or self.ask is not None:
            if self.bid is None or self.ask is None:
                raise ValueError("bid and ask must be provided together")
            BidAsk(self.bid, self.ask)


@dataclass(frozen=True)
class DataRequest:
    strategy: str
    name: str
    symbols: tuple[str, ...]
    interval: str
    lookback: int

    def __post_init__(self) -> None:
        if not self.strategy:
            raise ValueError("strategy is required")
        if not self.name:
            raise ValueError("data window name is required")
        if self.interval not in SUPPORTED_INTERVALS:
            raise ValueError(f"unsupported interval: {self.interval}")
        if self.lookback < 1:
            raise ValueError("lookback must be >= 1")
        object.__setattr__(self, "symbols", tuple(require_symbol(s) for s in self.symbols))

    @property
    def key(self) -> str:
        return f"{self.strategy}:{self.name}:{self.interval}:{self.lookback}"


@dataclass(frozen=True)
class BarWindow:
    name: str
    symbols: tuple[str, ...]
    interval: str
    values: np.ndarray

    def __post_init__(self) -> None:
        if self.interval not in SUPPORTED_INTERVALS:
            raise ValueError(f"unsupported interval: {self.interval}")
        arr = np.asarray(self.values, dtype=float)
        if arr.ndim != 2:
            raise ValueError("bar window values must be 2D")
        if arr.shape[1] != len(self.symbols):
            raise ValueError("bar window column count must match symbols")
        if not np.all(np.isfinite(arr)) or not np.all(arr > 0):
            raise ValueError("bar window values must be finite and > 0")
        object.__setattr__(self, "symbols", tuple(require_symbol(s) for s in self.symbols))
        object.__setattr__(self, "values", arr.copy())


def coerce_warmup_rows(
    rows: Mapping[str, Sequence[float]],
    symbols: tuple[str, ...],
) -> np.ndarray:
    columns = []
    for symbol in symbols:
        if symbol not in rows:
            raise ValueError(f"missing warmup rows for {symbol}")
        columns.append([require_finite(v, f"{symbol} price", positive=True) for v in rows[symbol]])
    if not columns:
        return np.empty((0, 0), dtype=float)
    lengths = {len(col) for col in columns}
    if len(lengths) != 1:
        raise ValueError("warmup rows must have equal lengths")
    return np.column_stack(columns)
