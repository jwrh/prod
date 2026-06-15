"""Runtime composition root."""

from __future__ import annotations

from pathlib import Path

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
from runtime.recovery import BrokerRecovery
from runtime.risk_engine import RiskEngine
from runtime.scheduler import RuntimeScheduler
from runtime.supervisor import Supervisor


class RuntimeComponents:
    """Owns lifecycle for stateful runtime dependencies."""

    def __init__(self, *, data_hub: DataHub, recovery: BrokerRecovery, specs: tuple[StrategySpec, ...]) -> None:
        self.data_hub = data_hub
        self.recovery = recovery
        self.specs = specs

    async def start(self) -> None:
        await self.data_hub.connect()
        await self.data_hub.warmup(self.specs)
        await self.recovery.recover(self.specs)

    async def stop(self, reason: str) -> None:
        await self.data_hub.disconnect()


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
        broker = self._adapter_factory.build_broker(config.broker)
        data_hub = DataHub(self._adapter_factory.build_data(config.data))
        strategies = {spec.name: self._strategy_factory.load(spec) for spec in config.strategies}
        specs = {spec.name: spec for spec in config.strategies}
        execution = ExecutionEngine(broker)
        supervisor = Supervisor(
            strategies=strategies,
            specs=specs,
            broker=broker,
            data_hub=data_hub,
            context=ContextBuilder(),
            risk_engine=RiskEngine(),
            portfolio_engine=PortfolioEngine(),
            execution_engine=execution,
            events=JsonlEventSink(config.observability.log_dir),
            status=StatusWriter(config.observability.status_path),
        )
        return RuntimeApp(
            components=RuntimeComponents(
                data_hub=data_hub,
                recovery=BrokerRecovery(broker),
                specs=config.strategies,
            ),
            scheduler=RuntimeScheduler(config.strategies),
            supervisor=supervisor,
        )
