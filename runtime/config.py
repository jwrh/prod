"""Runtime configuration loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from domain.portfolio import RiskSpec, VenueRule
from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec


@dataclass(frozen=True, slots=True)
class AdapterConfig:
    adapter: str
    settings: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    log_dir: str = "logs/events"
    status_path: str = "logs/status.json"


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    data: AdapterConfig
    broker: AdapterConfig
    observability: ObservabilityConfig
    risk: RiskSpec
    strategies: tuple[StrategySpec, ...]


class RuntimeConfigLoader:
    """Owns conversion from raw YAML into runtime domain objects."""

    ROOT_KEYS = frozenset({"data", "broker", "observability", "risk", "strategies"})
    DATA_ADAPTERS = frozenset({"ibkr", "replay"})
    BROKER_ADAPTERS = frozenset({"alpaca", "paper"})
    STRATEGY_KEYS = frozenset(
        {"name", "class", "universe", "schedule", "data", "capital", "risk", "params", "allow_adoption"}
    )

    def load(self, path: str | Path = "config.yaml") -> RuntimeConfig:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        return self.from_mapping(raw)

    def from_mapping(self, raw: Mapping[str, Any]) -> RuntimeConfig:
        self._reject(raw, self.ROOT_KEYS, "root")
        root_risk = self._risk(raw.get("risk", {}) or {})
        strategies = self._required(raw, "strategies")
        if not strategies:
            raise ValueError("at least one strategy is required")
        loaded_strategies = tuple(self._strategy(row, root_risk) for row in strategies)
        seen_names: set[str] = set()
        symbol_owners: dict[str, str] = {}
        for spec in loaded_strategies:
            if spec.name in seen_names:
                raise ValueError(f"duplicate strategy name: {spec.name}")
            seen_names.add(spec.name)
            for symbol in spec.universe:
                if symbol in symbol_owners:
                    raise ValueError(f"symbol {symbol} appears in multiple strategy universes")
                symbol_owners[symbol] = spec.name
        return RuntimeConfig(
            data=self._adapter(self._required(raw, "data"), "data", self.DATA_ADAPTERS),
            broker=self._adapter(self._required(raw, "broker"), "broker", self.BROKER_ADAPTERS),
            observability=self._observability(raw.get("observability", {}) or {}),
            risk=root_risk,
            strategies=loaded_strategies,
        )

    def _strategy(self, raw: Mapping[str, Any], root_risk: RiskSpec) -> StrategySpec:
        self._reject(raw, self.STRATEGY_KEYS, "strategies[]")
        data = self._required(raw, "data")
        schedule = self._required(raw, "schedule")
        capital = self._required(raw, "capital")
        windows = tuple(
            DataWindowSpec(str(row["name"]), str(row["interval"]), int(row["lookback"]))
            for row in self._required(data, "windows")
        )
        return StrategySpec(
            name=str(self._required(raw, "name")),
            class_path=str(self._required(raw, "class")),
            universe=tuple(self._required(raw, "universe")),
            schedule=ScheduleSpec(str(self._required(schedule, "rebalance"))),
            data=StrategyDataSpec(windows),
            capital=CapitalSpec(amount=float(self._required(capital, "amount"))),
            risk=root_risk.merged_with(self._risk(raw.get("risk", {}) or {})),
            params=dict(raw.get("params", {}) or {}),
            allow_adoption=self._bool(raw.get("allow_adoption", False), "strategies[].allow_adoption"),
        )

    def _risk(self, raw: Mapping[str, Any]) -> RiskSpec:
        rules = raw.get("venue_rules", {}) or {}
        return RiskSpec(
            max_qty_per_order=raw.get("max_qty_per_order"),
            max_notional_per_order=raw.get("max_notional_per_order"),
            max_gross_notional=raw.get("max_gross_notional"),
            max_drawdown_pct=raw.get("max_drawdown_pct"),
            venue_rules={symbol: VenueRule(**value) for symbol, value in rules.items()},
        )

    def _adapter(self, raw: Mapping[str, Any], path: str, supported: frozenset[str]) -> AdapterConfig:
        adapter = str(self._required(raw, "adapter")).strip()
        if adapter not in supported:
            raise ValueError(f"unsupported {path} adapter: {adapter}")
        return AdapterConfig(
            adapter=adapter,
            settings={key: value for key, value in raw.items() if key != "adapter"},
        )

    def _observability(self, raw: Mapping[str, Any]) -> ObservabilityConfig:
        return ObservabilityConfig(
            log_dir=str(raw.get("log_dir", "logs/events")),
            status_path=str(raw.get("status_path", "logs/status.json")),
        )

    def _required(self, raw: Mapping[str, Any], key: str) -> Any:
        if key not in raw:
            raise ValueError(f"missing required key: {key}")
        return raw[key]

    def _bool(self, value: Any, path: str) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"{path} must be boolean")
        return value

    def _reject(self, raw: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
        extra = sorted(set(raw) - allowed)
        if extra:
            raise ValueError(f"{path} unsupported keys: {','.join(extra)}")
