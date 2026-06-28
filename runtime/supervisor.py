"""One-tick runtime coordinator."""

from __future__ import annotations

import asyncio
from typing import Mapping

from domain.market import INTERVAL_SECONDS
from domain.ports import BrokerPort, EventSink, StatusPort
from domain.portfolio import PortfolioTarget
from domain.strategy import Strategy, StrategySpec
from runtime.context import ContextBlocked, ContextBuilder, ContextReady
from runtime.data_hub import DataHub
from runtime.errors import BrokerAmbiguous
from runtime.execution_engine import ExecutionEngine
from runtime.portfolio_engine import PortfolioEngine
from runtime.reasons import ReasonCode
from runtime.risk_engine import RiskAllowed, RiskBlocked, RiskEngine, RiskFlatten
from runtime.scheduler import Tick
from runtime.strategy_runner import StrategyFailed, StrategyRunner, StrategySucceeded, StrategyTimedOut


class Supervisor:
    """Coordinates a single strategy tick across runtime components."""

    def __init__(
        self,
        *,
        strategies: Mapping[str, Strategy],
        specs: Mapping[str, StrategySpec],
        broker: BrokerPort,
        data_hub: DataHub,
        context: ContextBuilder,
        risk_engine: RiskEngine,
        portfolio_engine: PortfolioEngine,
        execution_engine: ExecutionEngine,
        events: EventSink,
        status: StatusPort,
        strategy_timeout_seconds: float = 5.0,
        strategy_runner: StrategyRunner | None = None,
    ) -> None:
        self._strategies = dict(strategies)
        self._specs = dict(specs)
        self._broker = broker
        self._data = data_hub
        self._contexts = context
        self._risk = risk_engine
        self._portfolio = portfolio_engine
        self._execution = execution_engine
        self._events = events
        self._status = status
        self._strategy_runner = strategy_runner or StrategyRunner(timeout_seconds=strategy_timeout_seconds)
        self.ready = True

    async def on_tick(self, tick: Tick) -> None:
        spec = self._specs[tick.strategy_name]
        self._events.record("tick_started", {"strategy": spec.name, "trigger": tick.trigger})
        broker = await self._broker.snapshot()
        data = self._data.snapshot(spec.name, tick.now)
        match self._contexts.build(spec, data, broker, tick.session, tick.trigger):
            case ContextBlocked(reason=reason):
                await self._handle_block(spec, tick, reason)
                return
            case ContextReady(context=context):
                pass
        match await self._strategy_runner.evaluate(self._strategies[spec.name], context):
            case StrategySucceeded(target=target):
                pass
            case StrategyTimedOut(reason=reason):
                self.ready = False
                self._events.record("decision", {"strategy": spec.name, "status": "failed", "error": reason})
                self._write_status(False, tick, reason)
                return
            case StrategyFailed(error=error):
                self.ready = False
                self._events.record("decision", {"strategy": spec.name, "status": "failed", "error": error})
                self._write_status(False, tick, ReasonCode.STRATEGY_FAILED)
                return
        self._events.record("decision", {"strategy": spec.name, "action": target.action, "reason": target.reason})
        match self._risk.check(spec, target, broker, data):
            case RiskFlatten(reason=reason):
                await self._execution.flatten(spec)
                self._events.record("cleanup", {"strategy": spec.name, "reason": reason})
                self._write_status(True, tick, reason)
            case RiskBlocked(reason=reason):
                self._events.record("risk_block", {"strategy": spec.name, "reason": reason})
                self._write_status(True, tick, reason)
            case RiskAllowed():
                await self._execute_target(spec, target, broker, data.prices, tick)

    async def shutdown(self, reason: str) -> None:
        await asyncio.gather(*(self._execution.flatten(spec) for spec in self._specs.values()))
        self._status.write({"ready": True, "status": "stopped", "reason": reason})

    async def _execute_target(self, spec: StrategySpec, target: PortfolioTarget, broker, prices, tick: Tick) -> None:
        try:
            intents = self._portfolio.diff(spec, target, broker, prices=prices, batch_key=self._batch_key(spec, tick))
            fills = await self._execution.execute(spec, intents)
        except BrokerAmbiguous as exc:
            self.ready = False
            self._events.record("broker_ambiguous", {"strategy": spec.name, "error": str(exc)})
            self._write_status(False, tick, ReasonCode.BROKER_AMBIGUOUS)
            return
        self._events.record("orders_submitted", {"strategy": spec.name, "count": len(intents)})
        self._events.record("orders_filled", {"strategy": spec.name, "count": len(fills)})
        self._write_status(True, tick, None)

    async def _handle_block(self, spec: StrategySpec, tick: Tick, reason: str) -> None:
        self._events.record("risk_block", {"strategy": spec.name, "reason": reason})
        self._write_status(True, tick, reason)

    def _write_status(self, ready: bool, tick: Tick, reason: str | None) -> None:
        payload = {
            "ready": ready,
            "status": "running",
            "last_tick": tick.strategy_name,
            "trigger": tick.trigger,
            "active_strategies": sorted(self._strategies),
        }
        if reason:
            payload["last_block"] = reason
        self._status.write(payload)
        self._events.record("status", payload)

    def _batch_key(self, spec: StrategySpec, tick: Tick) -> str:
        return f"{tick.session.isoformat()}:{tick.strategy_name}:{tick.trigger}:{self._schedule_bucket(spec, tick)}"

    def _schedule_bucket(self, spec: StrategySpec, tick: Tick) -> int:
        seconds = INTERVAL_SECONDS[spec.schedule.rebalance]
        if seconds >= 86_400:
            return tick.session.toordinal()
        return int(tick.now.timestamp()) // seconds
