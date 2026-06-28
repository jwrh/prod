"""Broker-truth portfolio target diffing."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Mapping

from domain.orders import OrderIntent
from domain.portfolio import BrokerSnapshot, PortfolioTarget, Position, VenueRule
from domain.strategy import StrategySpec

MIN_DELTA_SHARES = 0.01


@dataclass(frozen=True, slots=True)
class PortfolioLine:
    """One symbol's planned portfolio change."""

    strategy_name: str
    symbol: str
    current_qty: float
    target_qty: float
    price: float
    rule: VenueRule

    @property
    def delta(self) -> float:
        return self.target_qty - self.current_qty

    @property
    def material(self) -> bool:
        return abs(self.delta) >= MIN_DELTA_SHARES

    @property
    def side(self) -> str:
        return "buy" if self.delta > 0 else "sell"

    @property
    def planned_qty(self) -> float:
        match self.delta > 0, self.rule.longs_fractional_ok:
            case True, True:
                return self._fractional_qty(self.delta)
            case True, False:
                return self._whole_lot_qty(self.delta)
            case False, _ if self._reduces_fractional_long:
                return self._fractional_qty(abs(self.delta))
            case False, _:
                return self._whole_lot_qty(abs(self.delta))

    @property
    def planned_notional(self) -> float:
        return round(self.planned_qty * self.price, 2)

    @property
    def entry_notional(self) -> float:
        return round(self.entry_qty * self.price, 2)

    @property
    def entry_qty(self) -> float:
        match self.delta > 0:
            case True if self.target_qty > 0:
                return max(self.planned_qty - max(-self.current_qty, 0.0), 0.0)
            case False if self.target_qty < 0:
                return max(self.planned_qty - max(self.current_qty, 0.0), 0.0)
            case _:
                return 0.0

    @property
    def _reduces_fractional_long(self) -> bool:
        return self.rule.longs_fractional_ok and self.current_qty > 0 and self.target_qty >= 0

    @property
    def _buy_notional_ok(self) -> bool:
        return self.entry_qty == 0 or self.entry_notional >= self.rule.min_notional

    @property
    def _sell_notional_ok(self) -> bool:
        return self.entry_qty == 0 or self.entry_notional >= self.rule.min_notional

    def intents(self, batch_key: str = "manual") -> tuple[OrderIntent, ...]:
        if not self.material:
            return ()
        client_order_id = self._client_order_id(batch_key)
        match self.delta > 0, self.rule.longs_fractional_ok:
            case True, True:
                qty = self.planned_qty
                return (
                    OrderIntent(self.symbol, "buy", qty=qty, client_order_id=client_order_id),
                ) if qty > 0 and self._buy_notional_ok else ()
            case True, False:
                qty = self.planned_qty
                return (
                    OrderIntent(self.symbol, "buy", qty=qty, client_order_id=client_order_id),
                ) if qty >= self.rule.min_qty and self._buy_notional_ok else ()
            case False, _ if self._reduces_fractional_long:
                qty = self.planned_qty
                return (
                    OrderIntent(self.symbol, "sell", qty=qty, client_order_id=client_order_id),
                ) if qty > 0 else ()
            case False, _:
                qty = self.planned_qty
                return (
                    OrderIntent(self.symbol, "sell", qty=qty, client_order_id=client_order_id),
                ) if qty >= self.rule.min_qty and self._sell_notional_ok else ()

    def _whole_lot_qty(self, qty: float) -> float:
        return math.floor(qty / self.rule.lot_size) * self.rule.lot_size

    def _fractional_qty(self, qty: float) -> float:
        return math.floor(qty * 1_000_000) / 1_000_000

    def _client_order_id(self, batch_key: str) -> str:
        raw = "|".join(
            (
                self.strategy_name,
                batch_key,
                self.symbol,
                self.side,
                f"{self.planned_qty:.8f}",
                f"{self.planned_notional:.2f}",
            )
        )
        return f"prod-{sha256(raw.encode('utf-8')).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class PortfolioPlan:
    """Target, broker, and price state for one portfolio diff."""

    spec: StrategySpec
    broker: BrokerSnapshot
    prices: Mapping[str, float]
    weights: Mapping[str, float] = field(default_factory=dict)
    notionals: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_target(
        cls,
        spec: StrategySpec,
        target: PortfolioTarget,
        broker: BrokerSnapshot,
        *,
        prices: Mapping[str, float],
    ) -> "PortfolioPlan":
        match target.action:
            case "hold":
                weights = {}
            case "flat":
                weights = {symbol: 0.0 for symbol in spec.universe}
            case "target":
                weights = {symbol: float(target.weights.get(symbol, 0.0)) for symbol in spec.universe}
            case _:
                raise ValueError(f"unsupported target action: {target.action}")
        return cls(spec, broker, prices, weights, cls.notionals_for(weights, spec.capital.amount))

    @staticmethod
    def notionals_for(weights: Mapping[str, float], capital: float) -> dict[str, float]:
        longs = {symbol: weight for symbol, weight in weights.items() if weight > 0}
        shorts = {symbol: abs(weight) for symbol, weight in weights.items() if weight < 0}
        match bool(longs), bool(shorts):
            case True, True:
                return {
                    **{symbol: capital * 0.5 * weight / sum(longs.values()) for symbol, weight in longs.items()},
                    **{symbol: capital * 0.5 * weight / sum(shorts.values()) for symbol, weight in shorts.items()},
                }
            case _:
                total = sum(abs(weight) for weight in weights.values())
                return {symbol: capital * abs(weight) / total for symbol, weight in weights.items()} if total > 0 else {}

    @property
    def symbols(self) -> tuple[str, ...]:
        return self.spec.universe

    def current_qty(self, symbol: str) -> float:
        return self.broker.positions.get(symbol, Position(symbol, 0.0)).qty

    def target_qty(self, symbol: str) -> float:
        weight = self.weights.get(symbol, 0.0)
        return math.copysign(self.notionals.get(symbol, 0.0) / self.price(symbol), weight) if weight else 0.0

    def delta(self, symbol: str) -> float:
        return self.target_qty(symbol) - self.current_qty(symbol)

    def line(self, symbol: str) -> PortfolioLine:
        return PortfolioLine(
            self.spec.name,
            symbol,
            self.current_qty(symbol),
            self.target_qty(symbol),
            self.price(symbol),
            self.venue_rule(symbol),
        )

    def lines(self) -> tuple[PortfolioLine, ...]:
        return tuple(self.line(symbol) for symbol in self.symbols)

    def intents(self, batch_key: str = "manual") -> tuple[OrderIntent, ...]:
        intents = (
            intent
            for line in self.lines()
            for intent in line.intents(batch_key)
        )
        return tuple(sorted(intents, key=lambda order: 0 if order.side == "sell" else 1))

    def price(self, symbol: str) -> float:
        match self.prices.get(symbol):
            case int() | float() as price if price > 0 and math.isfinite(price):
                return float(price)
            case _ if self.weights.get(symbol, 0.0) != 0.0 or self.current_qty(symbol) != 0.0:
                raise ValueError(f"{symbol}: missing price")
            case _:
                return 0.0

    def venue_rule(self, symbol: str) -> VenueRule:
        return self.spec.risk.venue_rules.get(symbol, VenueRule())


class PortfolioEngine:
    """Converts target weights into deterministic order intents."""

    def diff(
        self,
        spec: StrategySpec,
        target: PortfolioTarget,
        broker: BrokerSnapshot,
        *,
        prices: dict[str, float],
        batch_key: str = "manual",
    ) -> list[OrderIntent]:
        plan = PortfolioPlan.from_target(spec, target, broker, prices=prices)
        return list(plan.intents(batch_key))
