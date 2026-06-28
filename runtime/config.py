"""Runtime configuration loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from domain.market import require_finite, require_integer, require_string, require_string_sequence, require_symbol
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
    mode: str
    data: AdapterConfig
    broker: AdapterConfig
    observability: ObservabilityConfig
    risk: RiskSpec
    strategies: tuple[StrategySpec, ...]


class RuntimeConfigLoader:
    """Owns conversion from raw YAML into runtime domain objects."""

    ROOT_KEYS = frozenset({"mode", "data", "broker", "observability", "risk", "strategies"})
    MODES = frozenset({"replay", "sandbox", "live"})
    DATA_ADAPTERS = frozenset({"ibkr", "replay"})
    BROKER_ADAPTERS = frozenset({"alpaca", "paper"})
    STRATEGY_KEYS = frozenset(
        {"name", "class", "universe", "schedule", "data", "capital", "risk", "params", "allow_adoption"}
    )
    OBSERVABILITY_KEYS = frozenset({"log_dir", "status_path"})
    RISK_KEYS = frozenset(
        {"max_qty_per_order", "max_notional_per_order", "max_gross_notional", "max_drawdown_pct", "venue_rules"}
    )
    STRATEGY_DATA_KEYS = frozenset({"windows"})
    DATA_WINDOW_KEYS = frozenset({"name", "interval", "lookback"})
    SCHEDULE_KEYS = frozenset({"rebalance"})
    CAPITAL_KEYS = frozenset({"mode", "amount"})

    def load(self, path: str | Path = "config.yaml") -> RuntimeConfig:
        return self.from_mapping(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def from_mapping(self, raw: Any) -> RuntimeConfig:
        if not isinstance(raw, Mapping):
            raise ValueError("root must be a mapping")
        self._reject(raw, self.ROOT_KEYS, "root")
        mode = self._mode(self._required(raw, "mode"))
        data = self._adapter(self._section(raw, "data", required=True), "data", self.DATA_ADAPTERS)
        broker = self._adapter(self._section(raw, "broker", required=True), "broker", self.BROKER_ADAPTERS)
        self._validate_mode(mode, data, broker)
        root_risk = self._risk(self._section(raw, "risk", default={}))
        strategies = self._strategies(raw)
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
            mode=mode,
            data=data,
            broker=broker,
            observability=self._observability(self._section(raw, "observability", default={})),
            risk=root_risk,
            strategies=loaded_strategies,
        )

    def _strategies(self, raw: Mapping[str, Any]) -> tuple[Any, ...]:
        strategies = self._required(raw, "strategies")
        if strategies is None:
            raise ValueError("strategies cannot be null")
        if not isinstance(strategies, list):
            raise ValueError("strategies must be a list")
        if not strategies:
            raise ValueError("at least one strategy is required")
        return tuple(strategies)

    def _strategy(self, raw: Any, root_risk: RiskSpec) -> StrategySpec:
        if raw is None:
            raise ValueError("strategies[] cannot be null")
        if not isinstance(raw, Mapping):
            raise ValueError("strategies[] must be a mapping")
        self._reject(raw, self.STRATEGY_KEYS, "strategies[]")
        data = self._section(raw, "data", required=True, path="strategies[].data")
        schedule = self._section(raw, "schedule", required=True, path="strategies[].schedule")
        capital = self._section(raw, "capital", required=True, path="strategies[].capital")
        self._reject(data, self.STRATEGY_DATA_KEYS, "strategies[].data")
        self._reject(schedule, self.SCHEDULE_KEYS, "strategies[].schedule")
        self._reject(capital, self.CAPITAL_KEYS, "strategies[].capital")
        windows = tuple(self._data_window(row) for row in self._data_windows(data))
        return StrategySpec(
            name=self._required_str(raw, "name", "strategies[].name"),
            class_path=self._required_str(raw, "class", "strategies[].class"),
            universe=self._string_list(raw, "universe", "strategies[].universe"),
            schedule=ScheduleSpec(self._required_str(schedule, "rebalance", "strategies[].schedule.rebalance")),
            data=StrategyDataSpec(windows),
            capital=CapitalSpec(
                amount=self._required_float(capital, "amount", "strategies[].capital.amount"),
                mode=str(capital.get("mode", "notional")),
            ),
            risk=self._risk(self._section(raw, "risk", default={}), base=root_risk),
            params=self._section(raw, "params", default={}, path="strategies[].params"),
            allow_adoption=self._bool(raw.get("allow_adoption", False), "strategies[].allow_adoption"),
        )

    def _data_windows(self, raw: Mapping[str, Any]) -> tuple[Any, ...]:
        windows = self._required(raw, "windows")
        if windows is None:
            raise ValueError("strategies[].data.windows cannot be null")
        if not isinstance(windows, list):
            raise ValueError("strategies[].data.windows must be a list")
        return tuple(windows)

    def _data_window(self, raw: Any) -> DataWindowSpec:
        if raw is None:
            raise ValueError("strategies[].data.windows[] cannot be null")
        if not isinstance(raw, Mapping):
            raise ValueError("strategies[].data.windows[] must be a mapping")
        self._reject(raw, self.DATA_WINDOW_KEYS, "strategies[].data.windows[]")
        return DataWindowSpec(
            self._required_str(raw, "name", "strategies[].data.windows[].name"),
            self._required_str(raw, "interval", "strategies[].data.windows[].interval"),
            self._required_int(raw, "lookback", "strategies[].data.windows[].lookback"),
        )

    def _risk(self, raw: Mapping[str, Any], *, base: RiskSpec | None = None) -> RiskSpec:
        self._reject(raw, self.RISK_KEYS, "risk")
        rules = self._section(raw, "venue_rules", default={}, path="risk.venue_rules")
        base_rules = base.venue_rules if base is not None else {}
        return RiskSpec(
            max_qty_per_order=self._risk_limit(raw, "max_qty_per_order", base),
            max_notional_per_order=self._risk_limit(raw, "max_notional_per_order", base),
            max_gross_notional=self._risk_limit(raw, "max_gross_notional", base),
            max_drawdown_pct=self._risk_limit(raw, "max_drawdown_pct", base),
            venue_rules=self._venue_rules(rules, base_rules),
        )

    def _venue_rules(self, rules: Mapping[str, Any], base_rules: Mapping[str, VenueRule]) -> dict[str, VenueRule]:
        venue_rules, seen = dict(base_rules), set()
        for raw_symbol, value in rules.items():
            symbol = require_symbol(raw_symbol)
            if symbol in seen:
                raise ValueError(f"duplicate venue rule symbol: {symbol}")
            venue_rules[symbol] = base_rules.get(symbol, VenueRule()).with_overrides(
                self._venue_rule(value, f"risk.venue_rules.{raw_symbol}")
            )
            seen.add(symbol)
        return venue_rules

    def _venue_rule(self, raw: Any, path: str) -> Mapping[str, Any]:
        if raw is None:
            raise ValueError(f"{path} cannot be null")
        if not isinstance(raw, Mapping):
            raise ValueError(f"{path} must be a mapping")
        return raw

    def _risk_limit(self, raw: Mapping[str, Any], key: str, base: RiskSpec | None) -> Any:
        if key in raw:
            if raw[key] is None:
                raise ValueError(f"risk.{key} cannot be null")
            return raw[key]
        return getattr(base, key) if base is not None else None

    def _section(
        self,
        raw: Mapping[str, Any],
        key: str,
        *,
        default: Mapping[str, Any] | None = None,
        required: bool = False,
        path: str | None = None,
    ) -> Mapping[str, Any]:
        if key not in raw:
            if required:
                raise ValueError(f"missing required key: {key}")
            return default or {}
        value = raw[key]
        section_path = path or key
        if value is None:
            raise ValueError(f"{section_path} cannot be null")
        if not isinstance(value, Mapping):
            raise ValueError(f"{section_path} must be a mapping")
        return value

    def _adapter(self, raw: Mapping[str, Any], path: str, supported: frozenset[str]) -> AdapterConfig:
        adapter = str(self._required(raw, "adapter")).strip()
        if adapter not in supported:
            raise ValueError(f"unsupported {path} adapter: {adapter}")
        return AdapterConfig(
            adapter=adapter,
            settings={key: value for key, value in raw.items() if key != "adapter"},
        )

    def _observability(self, raw: Mapping[str, Any]) -> ObservabilityConfig:
        self._reject(raw, self.OBSERVABILITY_KEYS, "observability")
        return ObservabilityConfig(
            log_dir=self._optional_str(raw, "log_dir", "logs/events", "observability.log_dir"),
            status_path=self._optional_str(raw, "status_path", "logs/status.json", "observability.status_path"),
        )

    def _optional_str(self, raw: Mapping[str, Any], key: str, default: str, path: str) -> str:
        if key not in raw:
            return default
        value = raw[key]
        if value is None:
            raise ValueError(f"{path} cannot be null")
        return require_string(value, path)

    def _mode(self, value: Any) -> str:
        mode = str(value).strip()
        if mode not in self.MODES:
            raise ValueError(f"unsupported runtime mode: {mode}")
        return mode

    def _validate_mode(self, mode: str, data: AdapterConfig, broker: AdapterConfig) -> None:
        paper = self._broker_paper(broker)
        match mode:
            case "replay":
                if data.adapter != "replay" or broker.adapter != "paper":
                    raise ValueError("replay mode requires replay data and paper broker")
            case "sandbox":
                if data.adapter == "replay":
                    raise ValueError("sandbox mode requires non-replay data")
                if broker.adapter not in {"paper", "alpaca"} or not paper:
                    raise ValueError("sandbox mode requires paper broker execution")
            case "live":
                if data.adapter == "replay":
                    raise ValueError("live mode requires non-replay data")
                if broker.adapter != "alpaca" or paper:
                    raise ValueError("live mode requires alpaca broker with paper=false")
            case _:
                raise AssertionError(mode)

    def _broker_paper(self, broker: AdapterConfig) -> bool:
        if broker.adapter == "paper":
            return True
        return self._bool(broker.settings.get("paper", True), "broker.paper")

    def _required(self, raw: Mapping[str, Any], key: str) -> Any:
        if key not in raw:
            raise ValueError(f"missing required key: {key}")
        return raw[key]

    def _string_list(self, raw: Mapping[str, Any], key: str, path: str) -> tuple[str, ...]:
        value = self._required(raw, key)
        if value is None:
            raise ValueError(f"{path} cannot be null")
        return require_string_sequence(value, path)

    def _required_str(self, raw: Mapping[str, Any], key: str, path: str) -> str:
        value = self._required(raw, key)
        if value is None:
            raise ValueError(f"{path} cannot be null")
        return require_string(value, path)

    def _required_int(self, raw: Mapping[str, Any], key: str, path: str) -> int:
        value = self._required(raw, key)
        if value is None:
            raise ValueError(f"{path} cannot be null")
        return require_integer(value, path)

    def _required_float(self, raw: Mapping[str, Any], key: str, path: str) -> float:
        value = self._required(raw, key)
        if value is None:
            raise ValueError(f"{path} cannot be null")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{path} must be a number")
        return require_finite(value, path, positive=True)

    def _bool(self, value: Any, path: str) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"{path} must be boolean")
        return value

    def _reject(self, raw: Mapping[str, Any], allowed: frozenset[str], path: str) -> None:
        extra = sorted(str(key) for key in set(raw) - allowed)
        if extra:
            raise ValueError(f"{path} unsupported keys: {','.join(extra)}")
