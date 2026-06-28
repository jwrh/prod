"""Runtime composition root."""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from domain.ports import EventSink, StatusPort
from domain.strategy import StrategySpec
from observability.events import JsonlEventSink
from observability.status import StatusWriter
from runtime.app import RuntimeApp
from runtime.config import RuntimeConfig, RuntimeConfigLoader
from runtime.context import ContextBuilder
from runtime.data_hub import DataHub
from runtime.execution_engine import ExecutionEngine
from runtime.factories import AdapterFactory, StrategyFactory
from runtime.portfolio_engine import PortfolioEngine
from runtime.recovery import BrokerRecovery, RecoveryResult
from runtime.risk_engine import RiskEngine
from runtime.scheduler import RuntimeScheduler
from runtime.supervisor import Supervisor


class RuntimeComponents:
    """Owns lifecycle for stateful runtime dependencies."""

    def __init__(
        self,
        *,
        data_hub: DataHub,
        recovery: BrokerRecovery,
        specs: tuple[StrategySpec, ...],
        events: EventSink | None = None,
        status: StatusPort | None = None,
    ) -> None:
        self.data_hub = data_hub
        self.recovery = recovery
        self.specs = specs
        self.events = events
        self.status = status

    async def start(self) -> None:
        self._record_lifecycle("starting")
        await self.data_hub.connect()
        await self._prepare_runtime_state()

    async def reconnect(self) -> None:
        self._record_lifecycle("reconnecting")
        self.data_hub.mark_disconnected()
        await self.data_hub.disconnect()
        await self.data_hub.connect()
        await self._prepare_runtime_state()

    async def stop(self, reason: str) -> None:
        await self.data_hub.disconnect()

    async def _prepare_runtime_state(self) -> None:
        warmup = asyncio.create_task(self.data_hub.warmup(self.specs))
        recovery = asyncio.create_task(self.recovery.recover(self.specs))
        pending = {warmup, recovery}
        recovery_recorded = False
        try:
            while pending:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if recovery in done:
                    self._record_recovery(recovery.result())
                    recovery_recorded = True
                if warmup in done:
                    warmup.result()
        except BaseException:
            if not recovery_recorded and recovery.done() and not recovery.cancelled():
                try:
                    self._record_recovery(recovery.result())
                except BaseException:
                    pass
            for task in pending:
                task.cancel()
            await asyncio.gather(warmup, recovery, return_exceptions=True)
            raise

    def _record_lifecycle(self, status: str) -> None:
        if self.status is not None:
            self.status.write(
                {
                    "ready": False,
                    "status": status,
                    "active_strategies": sorted(spec.name for spec in self.specs),
                }
            )

    def _record_recovery(self, recovery: RecoveryResult | None) -> None:
        if recovery is None:
            return
        incidents = list(recovery.incidents)
        payload = {"clean": recovery.clean, "ready": recovery.ready, "incidents": incidents}
        if self.events is not None:
            self.events.record("recovery", payload)
        if self.status is not None:
            self.status.write(
                {
                    "ready": False,
                    "status": "running",
                    "active_strategies": sorted(spec.name for spec in self.specs),
                    "recovery_ready": recovery.ready,
                    "recovery_clean": recovery.clean,
                    "recovery_incidents": incidents,
                }
            )


class RuntimeCompositionRoot:
    """Assembles the runtime object graph from configuration."""

    def __init__(
        self,
        *,
        config_loader: RuntimeConfigLoader = RuntimeConfigLoader(),
        adapter_factory: AdapterFactory = AdapterFactory(),
        strategy_factory: StrategyFactory = StrategyFactory(),
    ) -> None:
        self._config_loader = config_loader
        self._adapter_factory = adapter_factory
        self._strategy_factory = strategy_factory

    def build_app(self, path: str | Path = "config.yaml") -> RuntimeApp:
        return self.from_config(self._config_loader.load(path))

    def from_config(self, config: RuntimeConfig) -> RuntimeApp:
        run_id = f"run-{uuid4().hex}"
        broker = self._adapter_factory.build_broker(config.broker)
        data_hub = DataHub(self._adapter_factory.build_data(config.data))
        strategies = {spec.name: self._strategy_factory.load(spec) for spec in config.strategies}
        specs = {spec.name: spec for spec in config.strategies}
        execution = ExecutionEngine(broker)
        events = JsonlEventSink(config.observability.log_dir, run_id=run_id, mode=config.mode)
        status = StatusWriter(config.observability.status_path, run_id=run_id, mode=config.mode)
        supervisor = Supervisor(
            strategies=strategies,
            specs=specs,
            broker=broker,
            data_hub=data_hub,
            context=ContextBuilder(),
            risk_engine=RiskEngine(),
            portfolio_engine=PortfolioEngine(),
            execution_engine=execution,
            events=events,
            status=status,
        )
        return RuntimeApp(
            components=RuntimeComponents(
                data_hub=data_hub,
                recovery=BrokerRecovery(broker),
                specs=config.strategies,
                events=events,
                status=status,
            ),
            scheduler=RuntimeScheduler(config.strategies),
            supervisor=supervisor,
        )
