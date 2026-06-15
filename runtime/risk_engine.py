"""Pre-trade and portfolio guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.portfolio import BrokerSnapshot, PortfolioTarget, VenueRule
from domain.strategy import StrategySpec
from runtime.data_hub import DataView


@dataclass(frozen=True, slots=True)
class RiskAllowed:
    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class RiskBlocked:
    reason: str

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class RiskFlatten:
    reason: str

    def __bool__(self) -> bool:
        return True


RiskDecision = RiskAllowed | RiskBlocked | RiskFlatten


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """Immutable risk-policy input for one strategy decision."""

    spec: StrategySpec
    target: PortfolioTarget
    broker: BrokerSnapshot
    data: DataView

    @property
    def universe(self) -> frozenset[str]:
        return frozenset(self.spec.universe)

    @property
    def target_symbols(self) -> frozenset[str]:
        return frozenset(self.target.weights)

    @property
    def outside_universe(self) -> frozenset[str]:
        return self.target_symbols - self.universe

    @property
    def unpriced_exposure(self) -> tuple[str, ...]:
        return tuple(
            symbol
            for symbol, position in self.broker.positions.items()
            if symbol in self.universe and position.qty != 0.0 and symbol not in self.data.prices
        )

    @property
    def missing_target_prices(self) -> frozenset[str]:
        return self.target_symbols - frozenset(self.data.prices)

    @property
    def unshortable_targets(self) -> tuple[str, ...]:
        return tuple(
            symbol
            for symbol, weight in self.target.weights.items()
            if weight < 0 and not self.venue_rule(symbol).shortable
        )

    @property
    def gross_notional_limit(self) -> float:
        return self.spec.risk.max_gross_notional or float("inf")

    @property
    def projected_gross_notional(self) -> float:
        gross_weight = min(1.0, sum(abs(weight) for weight in self.target.weights.values()))
        return self.spec.capital.amount * gross_weight

    def venue_rule(self, symbol: str) -> VenueRule:
        return self.spec.risk.venue_rules.get(symbol, VenueRule())


class RiskRule(Protocol):
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision: ...


@dataclass(frozen=True, slots=True)
class FreshTargetDataRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        match assessment.target.action, assessment.data.fresh:
            case "target", False:
                return RiskBlocked(assessment.data.block_reason or "stale_quotes")
            case _:
                return RiskAllowed()


@dataclass(frozen=True, slots=True)
class UnpricedExposureRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return RiskFlatten("missing_position_price") if assessment.unpriced_exposure else RiskAllowed()


@dataclass(frozen=True, slots=True)
class TargetUniverseRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return RiskBlocked("target_outside_universe") if assessment.outside_universe else RiskAllowed()


@dataclass(frozen=True, slots=True)
class ShortSaleRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return RiskBlocked("short_not_allowed") if assessment.unshortable_targets else RiskAllowed()


@dataclass(frozen=True, slots=True)
class MissingTargetPriceRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return RiskBlocked("missing_prices") if assessment.missing_target_prices else RiskAllowed()


@dataclass(frozen=True, slots=True)
class GrossNotionalRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return (
            RiskBlocked("max_gross_notional")
            if assessment.projected_gross_notional > assessment.gross_notional_limit
            else RiskAllowed()
        )


class RiskEngine:
    """Evaluates ordered risk policy objects for one strategy decision."""

    DEFAULT_RULES: tuple[RiskRule, ...] = (
        FreshTargetDataRule(),
        UnpricedExposureRule(),
        TargetUniverseRule(),
        ShortSaleRule(),
        MissingTargetPriceRule(),
        GrossNotionalRule(),
    )

    def __init__(self, rules: tuple[RiskRule, ...] = DEFAULT_RULES) -> None:
        self._rules = rules

    @property
    def rules(self) -> tuple[RiskRule, ...]:
        return self._rules

    def check(
        self,
        spec: StrategySpec,
        target: PortfolioTarget,
        broker: BrokerSnapshot,
        data: DataView,
    ) -> RiskDecision:
        assessment = RiskAssessment(spec, target, broker, data)
        return next(filter(None, (rule.evaluate(assessment) for rule in self._rules)), RiskAllowed())
