"""Pre-trade and portfolio guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from domain.portfolio import BrokerSnapshot, PortfolioTarget, VenueRule
from domain.strategy import StrategySpec
from runtime.data_hub import DataView
from runtime.portfolio_engine import PortfolioLine, PortfolioPlan
from runtime.reasons import ReasonCode


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
    high_water_equity: float | None = None

    def __post_init__(self) -> None:
        if self.high_water_equity is None:
            object.__setattr__(self, "high_water_equity", self.broker.account.equity)

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
        if self.missing_target_prices:
            return ()
        return tuple(
            line.symbol
            for line in self.planned_order_lines
            if line.target_qty < 0 and line.entry_qty > 0 and not line.rule.shortable
        )

    @property
    def gross_notional_limit(self) -> float:
        return self.spec.risk.max_gross_notional or float("inf")

    @property
    def projected_gross_notional(self) -> float:
        return sum(abs(notional) for notional in self.portfolio_plan.notionals.values())

    @property
    def max_order_notional(self) -> float:
        return self.spec.risk.max_notional_per_order or float("inf")

    @property
    def max_order_qty(self) -> float:
        return self.spec.risk.max_qty_per_order or float("inf")

    @property
    def max_drawdown_pct(self) -> float:
        return self.spec.risk.max_drawdown_pct or float("inf")

    @property
    def drawdown_pct(self) -> float:
        return max(0.0, (self.high_water_equity - self.broker.account.equity) / self.high_water_equity * 100.0)

    @property
    def portfolio_plan(self) -> PortfolioPlan:
        return PortfolioPlan.from_target(self.spec, self.target, self.broker, prices=self.data.prices)

    @property
    def planned_order_lines(self) -> tuple[PortfolioLine, ...]:
        return tuple(line for line in self.portfolio_plan.lines() if line.intents("risk-check"))

    def venue_rule(self, symbol: str) -> VenueRule:
        return self.spec.risk.venue_rules.get(symbol, VenueRule())


class RiskRule(Protocol):
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision: ...


@dataclass(frozen=True, slots=True)
class FreshTargetDataRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        match assessment.target.action, assessment.data.fresh:
            case "target", False:
                return RiskBlocked(assessment.data.block_reason or ReasonCode.STALE_QUOTES)
            case _:
                return RiskAllowed()


@dataclass(frozen=True, slots=True)
class UnpricedExposureRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return RiskFlatten(ReasonCode.MISSING_POSITION_PRICE) if assessment.unpriced_exposure else RiskAllowed()


@dataclass(frozen=True, slots=True)
class TargetUniverseRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return RiskBlocked(ReasonCode.TARGET_OUTSIDE_UNIVERSE) if assessment.outside_universe else RiskAllowed()


@dataclass(frozen=True, slots=True)
class ShortSaleRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return RiskBlocked(ReasonCode.SHORT_NOT_ALLOWED) if assessment.unshortable_targets else RiskAllowed()


@dataclass(frozen=True, slots=True)
class MissingTargetPriceRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return RiskBlocked(ReasonCode.MISSING_PRICES) if assessment.missing_target_prices else RiskAllowed()


@dataclass(frozen=True, slots=True)
class GrossNotionalRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return (
            RiskBlocked(ReasonCode.MAX_GROSS_NOTIONAL)
            if assessment.projected_gross_notional > assessment.gross_notional_limit
            else RiskAllowed()
        )


@dataclass(frozen=True, slots=True)
class MaxOrderNotionalRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return (
            RiskBlocked(ReasonCode.MAX_NOTIONAL_PER_ORDER)
            if any(line.planned_notional > assessment.max_order_notional for line in assessment.planned_order_lines)
            else RiskAllowed()
        )


@dataclass(frozen=True, slots=True)
class VenueMaxOrderNotionalRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return (
            RiskBlocked(ReasonCode.MAX_NOTIONAL_PER_ORDER)
            if any(
                line.rule.max_notional_per_order is not None
                and line.planned_notional > line.rule.max_notional_per_order
                for line in assessment.planned_order_lines
            )
            else RiskAllowed()
        )


@dataclass(frozen=True, slots=True)
class MaxOrderQtyRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return (
            RiskBlocked(ReasonCode.MAX_QTY_PER_ORDER)
            if any(line.planned_qty > assessment.max_order_qty for line in assessment.planned_order_lines)
            else RiskAllowed()
        )


@dataclass(frozen=True, slots=True)
class DrawdownRule:
    def evaluate(self, assessment: RiskAssessment) -> RiskDecision:
        return (
            RiskFlatten(ReasonCode.MAX_DRAWDOWN_PCT)
            if assessment.drawdown_pct > assessment.max_drawdown_pct
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
        DrawdownRule(),
        GrossNotionalRule(),
        VenueMaxOrderNotionalRule(),
        MaxOrderNotionalRule(),
        MaxOrderQtyRule(),
    )

    def __init__(self, rules: tuple[RiskRule, ...] = DEFAULT_RULES) -> None:
        self._rules = rules
        self._high_water_equity: dict[str, float] = {}

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
        assessment = RiskAssessment(spec, target, broker, data, self._high_water(spec.name, broker.account.equity))
        return next(filter(None, (rule.evaluate(assessment) for rule in self._rules)), RiskAllowed())

    def _high_water(self, strategy_name: str, equity: float) -> float:
        high_water = max(self._high_water_equity.get(strategy_name, equity), equity)
        self._high_water_equity[strategy_name] = high_water
        return high_water
