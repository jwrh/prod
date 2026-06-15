"""Dummy strategy for examples, tests, and public repository defaults."""

from __future__ import annotations

from domain.portfolio import PortfolioTarget
from domain.strategy import StrategyContext, StrategySpec


class DummyStrategy:
    """Returns static configured weights and flattens on shutdown triggers."""

    def __init__(self, spec: StrategySpec) -> None:
        self.weights = {str(symbol): float(weight) for symbol, weight in spec.params.get("weights", {}).items()}

    def evaluate(self, ctx: StrategyContext) -> PortfolioTarget:
        if ctx.trigger in {"pre_close", "hard_cutoff", "shutdown"}:
            return PortfolioTarget.flat("risk_exit")
        if not self.weights:
            return PortfolioTarget.hold("no_dummy_weights")
        return PortfolioTarget.weights(self.weights, "dummy_target")
