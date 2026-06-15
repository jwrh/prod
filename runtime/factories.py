"""Runtime object factories for external adapters and strategies."""

from __future__ import annotations

import importlib
import os
from typing import Mapping

from adapters.broker.alpaca import AlpacaBroker
from adapters.data.ibkr import IBKRMarketData
from adapters.replay import ReplayBroker, ReplayMarketData
from domain.market import Quote
from domain.portfolio import AccountSnapshot, BrokerSnapshot
from domain.strategy import StrategySpec
from runtime.config import AdapterConfig


class StrategyFactory:
    """Loads configured strategy classes."""

    def load(self, spec: StrategySpec):
        module, name = spec.class_path.rsplit(".", 1)
        strategy_class = getattr(importlib.import_module(module), name)
        return strategy_class(spec)


class AdapterFactory:
    """Builds configured data and broker adapters."""

    def __init__(self, environ: Mapping[str, str] = os.environ) -> None:
        self._environ = environ

    def build_data(self, config: AdapterConfig):
        match config.adapter:
            case "ibkr":
                return IBKRMarketData(**config.settings)
            case "replay":
                return self._replay_data(config)
            case _:
                raise ValueError(f"unsupported data adapter: {config.adapter}")

    def build_broker(self, config: AdapterConfig):
        match config.adapter:
            case "alpaca":
                return AlpacaBroker(
                    api_key=self._env("ALPACA_API_KEY"),
                    api_secret=self._env("ALPACA_API_SECRET"),
                    paper=bool(config.settings.get("paper", True)),
                )
            case "paper":
                return ReplayBroker(BrokerSnapshot(AccountSnapshot(100_000.0, 100_000.0), positions={}))
            case _:
                raise ValueError(f"unsupported broker adapter: {config.adapter}")

    def _replay_data(self, config: AdapterConfig) -> ReplayMarketData:
        quotes = tuple(
            Quote(str(row["symbol"]), float(row["price"]))
            for row in config.settings.get("quotes", ())
        )
        return ReplayMarketData(warmup_rows=config.settings.get("warmup_rows", {}) or {}, quotes=quotes)

    def _env(self, name: str) -> str:
        value = self._environ.get(name, "").strip()
        if not value:
            raise ValueError(f"missing required environment variable: {name}")
        return value
