"""Build strategy-facing contexts from data and broker truth."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from domain.portfolio import BrokerSnapshot
from domain.strategy import StrategyContext, StrategySpec
from runtime.data_hub import DataView
from runtime.reasons import ReasonCode


@dataclass(frozen=True)
class ContextReady:
    context: StrategyContext


@dataclass(frozen=True)
class ContextBlocked:
    reason: str


ContextResult = ContextReady | ContextBlocked


class ContextBuilder:
    """Pure conversion layer: data view plus broker truth into strategy context."""

    def build(
        self,
        spec: StrategySpec,
        data: DataView,
        broker: BrokerSnapshot,
        session: date,
        trigger: str,
    ) -> ContextResult:
        if not data.ready:
            return ContextBlocked(data.block_reason or ReasonCode.DATA_UNAVAILABLE)
        current_weights = {
            symbol: round(position.qty * data.prices[symbol] / spec.capital.amount, 6)
            for symbol, position in broker.positions.items()
            if symbol in spec.universe and position.qty != 0.0 and symbol in data.prices
        }
        return ContextReady(
            StrategyContext(
                strategy=spec.name,
                now=data.now,
                session=session,
                trigger=trigger,
                universe=spec.universe,
                prices=data.prices,
                bid_ask=data.bid_ask,
                windows=data.windows,
                account=broker.account,
                positions=broker.positions,
                current_weights=current_weights,
            )
        )
