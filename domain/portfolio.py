"""Portfolio, risk, and target domain types."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal, Mapping

from domain.market import require_finite, require_symbol


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", require_symbol(self.symbol))
        object.__setattr__(self, "qty", require_finite(self.qty, "position qty"))


@dataclass(frozen=True)
class AccountSnapshot:
    equity: float
    cash: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "equity", require_finite(self.equity, "equity", positive=True))
        object.__setattr__(self, "cash", require_finite(self.cash, "cash"))


@dataclass(frozen=True)
class BrokerSnapshot:
    account: AccountSnapshot
    positions: Mapping[str, Position] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = {}
        for key, position in self.positions.items():
            symbol = require_symbol(key)
            if not isinstance(position, Position):
                raise ValueError(f"position {symbol} must be a Position")
            if position.symbol != symbol:
                raise ValueError(f"position key {symbol} does not match position symbol {position.symbol}")
            normalized[symbol] = position
        object.__setattr__(self, "positions", MappingProxyType(normalized))


@dataclass(frozen=True)
class VenueRule:
    longs_fractional_ok: bool = True
    shortable: bool = True
    lot_size: int = 1
    min_qty: int = 1
    min_notional: float = 1.0
    max_notional_per_order: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.longs_fractional_ok, bool):
            raise ValueError("longs_fractional_ok must be boolean")
        if not isinstance(self.shortable, bool):
            raise ValueError("shortable must be boolean")
        if isinstance(self.lot_size, bool) or not isinstance(self.lot_size, int):
            raise ValueError("lot_size must be an integer")
        if isinstance(self.min_qty, bool) or not isinstance(self.min_qty, int):
            raise ValueError("min_qty must be an integer")
        if self.lot_size < 1 or self.min_qty < 1:
            raise ValueError("lot_size and min_qty must be >= 1")
        object.__setattr__(self, "min_notional", require_finite(self.min_notional, "min_notional", positive=True))
        if self.max_notional_per_order is not None:
            object.__setattr__(
                self,
                "max_notional_per_order",
                require_finite(self.max_notional_per_order, "max_notional_per_order", positive=True),
            )

    def with_overrides(self, overrides: Mapping[str, object]) -> "VenueRule":
        allowed = {
            "longs_fractional_ok",
            "shortable",
            "lot_size",
            "min_qty",
            "min_notional",
            "max_notional_per_order",
        }
        extra = sorted(str(key) for key in set(overrides) - allowed)
        if extra:
            raise ValueError(f"venue rule unsupported keys: {','.join(extra)}")
        if "max_notional_per_order" in overrides and overrides["max_notional_per_order"] is None:
            raise ValueError("venue rule max_notional_per_order cannot be null")
        return replace(self, **dict(overrides))


@dataclass(frozen=True)
class RiskSpec:
    max_qty_per_order: float | None = None
    max_notional_per_order: float | None = None
    max_gross_notional: float | None = None
    max_drawdown_pct: float | None = None
    venue_rules: Mapping[str, VenueRule] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("max_qty_per_order", "max_notional_per_order", "max_gross_notional", "max_drawdown_pct"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, require_finite(value, name, positive=True))
        object.__setattr__(
            self,
            "venue_rules",
            MappingProxyType({require_symbol(k): v for k, v in self.venue_rules.items()}),
        )

    def merged_with(self, override: "RiskSpec") -> "RiskSpec":
        """Return this policy with explicitly configured strategy overrides."""
        return RiskSpec(
            max_qty_per_order=override.max_qty_per_order or self.max_qty_per_order,
            max_notional_per_order=override.max_notional_per_order or self.max_notional_per_order,
            max_gross_notional=override.max_gross_notional or self.max_gross_notional,
            max_drawdown_pct=override.max_drawdown_pct or self.max_drawdown_pct,
            venue_rules={**self.venue_rules, **override.venue_rules},
        )


class PortfolioTarget:
    """Strategy output: hold, target portfolio weights, or flat."""

    def __init__(
        self,
        action: Literal["hold", "target", "flat"],
        weights: Mapping[str, float],
        reason: str,
    ) -> None:
        if action not in {"hold", "target", "flat"}:
            raise ValueError(f"unsupported target action: {action}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        normalized = {}
        for key, value in weights.items():
            symbol = require_symbol(key)
            if symbol in normalized:
                raise ValueError(f"duplicate target symbol: {symbol}")
            normalized[symbol] = require_finite(value, f"weight {key}")
        if action in {"hold", "flat"} and normalized:
            raise ValueError(f"{action} cannot carry weights")
        self.action = action
        self.weights = MappingProxyType(normalized)
        self.reason = reason.strip()

    @classmethod
    def hold(cls, reason: str) -> "PortfolioTarget":
        return cls("hold", {}, reason)

    @classmethod
    def flat(cls, reason: str) -> "PortfolioTarget":
        return cls("flat", {}, reason)

    @classmethod
    def weights(cls, weights: Mapping[str, float], reason: str) -> "PortfolioTarget":
        return cls("target", weights, reason)
