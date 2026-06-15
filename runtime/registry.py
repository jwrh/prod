"""Backward-compatible runtime registry facade."""

from __future__ import annotations

from pathlib import Path

from domain.strategy import StrategySpec
from runtime.composition import RuntimeComponents, RuntimeCompositionRoot
from runtime.config import AdapterConfig, ObservabilityConfig, RuntimeConfig, RuntimeConfigLoader
from runtime.factories import AdapterFactory, StrategyFactory


def load_runtime_config(path: str | Path = "config.yaml") -> RuntimeConfig:
    return RuntimeConfigLoader().load(path)


def build_runtime_app(path: str | Path = "config.yaml"):
    return RuntimeCompositionRoot().build_app(path)


def load_strategy(spec: StrategySpec):
    return StrategyFactory().load(spec)


def build_data_adapter(config: AdapterConfig):
    return AdapterFactory().build_data(config)


def build_broker_adapter(config: AdapterConfig):
    return AdapterFactory().build_broker(config)
