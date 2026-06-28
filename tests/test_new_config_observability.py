from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def test_registry_loads_strict_config_and_rejects_unknown_keys(tmp_path, monkeypatch):
    import pytest

    from runtime.config import RuntimeConfigLoader
    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
observability:
  log_dir: logs/events
  status_path: logs/status.json
risk:
  max_gross_notional: 50000
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA, BBB]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    risk:
      venue_rules:
        AAA: {longs_fractional_ok: true, shortable: true}
        BBB: {longs_fractional_ok: true, shortable: true}
    params:
      weights: {AAA: 1.0}
""",
    )

    loaded = load_runtime_config(config)
    assert loaded.data.adapter == "replay"
    assert loaded.strategies[0].schedule.rebalance == "1m"

    bad = tmp_path / "bad.yaml"
    bad.write_text(config.read_text() + "\nextra: true\n")
    with pytest.raises(ValueError, match="unsupported keys"):
        load_runtime_config(bad)

    bad_root = tmp_path / "bad-root.yaml"
    bad_root.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be a mapping"):
        load_runtime_config(bad_root)

    null_root = tmp_path / "null-root.yaml"
    null_root.write_text("null", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be a mapping"):
        load_runtime_config(null_root)

    bad_observability = tmp_path / "bad-observability.yaml"
    bad_observability.write_text(config.read_text().replace("status_path:", "status_pat:", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="observability unsupported keys: status_pat"):
        load_runtime_config(bad_observability)

    null_observability = tmp_path / "null-observability.yaml"
    null_observability.write_text(config.read_text().replace("observability:\n  log_dir: logs/events\n  status_path: logs/status.json", "observability:"), encoding="utf-8")
    with pytest.raises(ValueError, match="observability cannot be null"):
        load_runtime_config(null_observability)

    null_observability_path = tmp_path / "null-observability-path.yaml"
    null_observability_path.write_text(config.read_text().replace("status_path: logs/status.json", "status_path:"), encoding="utf-8")
    with pytest.raises(ValueError, match="observability.status_path cannot be null"):
        load_runtime_config(null_observability_path)

    null_observability_dir = tmp_path / "null-observability-dir.yaml"
    null_observability_dir.write_text(config.read_text().replace("log_dir: logs/events", "log_dir:"), encoding="utf-8")
    with pytest.raises(ValueError, match="observability.log_dir cannot be null"):
        load_runtime_config(null_observability_dir)

    boolean_observability_dir = tmp_path / "boolean-observability-dir.yaml"
    boolean_observability_dir.write_text(config.read_text().replace("log_dir: logs/events", "log_dir: false"), encoding="utf-8")
    with pytest.raises(ValueError, match="observability.log_dir must be a string"):
        load_runtime_config(boolean_observability_dir)

    numeric_observability_path = tmp_path / "numeric-observability-path.yaml"
    numeric_observability_path.write_text(config.read_text().replace("status_path: logs/status.json", "status_path: 1.5"), encoding="utf-8")
    with pytest.raises(ValueError, match="observability.status_path must be a string"):
        load_runtime_config(numeric_observability_path)

    empty_observability_path = tmp_path / "empty-observability-path.yaml"
    empty_observability_path.write_text(config.read_text().replace("status_path: logs/status.json", "status_path: ''"), encoding="utf-8")
    with pytest.raises(ValueError, match="observability.status_path must be a string"):
        load_runtime_config(empty_observability_path)

    blank_observability_dir = tmp_path / "blank-observability-dir.yaml"
    blank_observability_dir.write_text(config.read_text().replace("log_dir: logs/events", "log_dir: '   '"), encoding="utf-8")
    with pytest.raises(ValueError, match="observability.log_dir must be a string"):
        load_runtime_config(blank_observability_dir)

    bad_risk = tmp_path / "bad-risk.yaml"
    bad_risk.write_text(config.read_text().replace("max_gross_notional:", "max_gros_notional:", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="risk unsupported keys: max_gros_notional"):
        load_runtime_config(bad_risk)
    with pytest.raises(ValueError, match="risk unsupported keys: 7"):
        RuntimeConfigLoader().from_mapping(
            {
                "mode": "replay",
                "data": {"adapter": "replay"},
                "broker": {"adapter": "paper"},
                "risk": {7: True},
                "strategies": [
                    {
                        "name": "demo",
                        "class": "strategies.dummy.DummyStrategy",
                        "universe": ["AAA"],
                        "schedule": {"rebalance": "1m"},
                        "data": {"windows": [{"name": "fast", "interval": "1m", "lookback": 2}]},
                        "capital": {"mode": "notional", "amount": 10000},
                    }
                ],
            }
        )

    null_data = tmp_path / "null-data.yaml"
    null_data.write_text(nested := """
mode: replay
data:
broker:
  adapter: paper
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""", encoding="utf-8")
    with pytest.raises(ValueError, match="data cannot be null"):
        load_runtime_config(null_data)

    null_broker = tmp_path / "null-broker.yaml"
    null_broker.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="broker cannot be null"):
        load_runtime_config(null_broker)

    nested = """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
"""
    null_strategies = tmp_path / "null-strategies.yaml"
    null_strategies.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
strategies:
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strategies cannot be null"):
        load_runtime_config(null_strategies)

    scalar_strategies = tmp_path / "scalar-strategies.yaml"
    scalar_strategies.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
strategies: demo
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strategies must be a list"):
        load_runtime_config(scalar_strategies)
    with pytest.raises(ValueError, match="strategies must be a list"):
        RuntimeConfigLoader().from_mapping(
            {
                "mode": "replay",
                "data": {"adapter": "replay"},
                "broker": {"adapter": "paper"},
                "strategies": (),
            }
        )

    null_strategy = tmp_path / "null-strategy.yaml"
    null_strategy.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
strategies:
  - null
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strategies\\[\\] cannot be null"):
        load_runtime_config(null_strategy)

    scalar_strategy = tmp_path / "scalar-strategy.yaml"
    scalar_strategy.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
strategies:
  - demo
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strategies\\[\\] must be a mapping"):
        load_runtime_config(scalar_strategy)

    scalar_universe = tmp_path / "scalar-universe.yaml"
    scalar_universe.write_text(nested.replace("universe: [AAA]", "universe: AB"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.universe must be a list"):
        load_runtime_config(scalar_universe)
    with pytest.raises(ValueError, match="strategies\\[\\]\\.universe must be a list"):
        RuntimeConfigLoader().from_mapping(
            {
                "mode": "replay",
                "data": {"adapter": "replay"},
                "broker": {"adapter": "paper"},
                "strategies": [
                    {
                        "name": "demo",
                        "class": "strategies.dummy.DummyStrategy",
                        "universe": ("AAA",),
                        "schedule": {"rebalance": "1m"},
                        "data": {"windows": [{"name": "fast", "interval": "1m", "lookback": 2}]},
                        "capital": {"mode": "notional", "amount": 10000},
                    }
                ],
            }
        )

    numeric_universe = tmp_path / "numeric-universe.yaml"
    numeric_universe.write_text(nested.replace("universe: [AAA]", "universe: [7]"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.universe must contain strings"):
        load_runtime_config(numeric_universe)

    null_params = tmp_path / "null-params.yaml"
    null_params.write_text(nested.replace("params: {weights: {AAA: 1.0}}", "params:"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.params cannot be null"):
        load_runtime_config(null_params)

    scalar_params = tmp_path / "scalar-params.yaml"
    scalar_params.write_text(nested.replace("params: {weights: {AAA: 1.0}}", "params: false"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.params must be a mapping"):
        load_runtime_config(scalar_params)

    bad_schedule = tmp_path / "bad-schedule.yaml"
    bad_schedule.write_text(nested.replace("schedule: {rebalance: 1m}", "schedule: {rebalance: 1m, typo: true}"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.schedule unsupported keys: typo"):
        load_runtime_config(bad_schedule)

    null_schedule = tmp_path / "null-schedule.yaml"
    null_schedule.write_text(nested.replace("schedule: {rebalance: 1m}", "schedule:"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.schedule cannot be null"):
        load_runtime_config(null_schedule)

    bad_strategy_data = tmp_path / "bad-strategy-data.yaml"
    bad_strategy_data.write_text(nested.replace("data:\n      windows:", "data:\n      extra: true\n      windows:"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data unsupported keys: extra"):
        load_runtime_config(bad_strategy_data)

    null_strategy_data = tmp_path / "null-strategy-data.yaml"
    null_strategy_data.write_text(nested.replace("data:\n      windows:\n        - {name: fast, interval: 1m, lookback: 2}", "data:"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data cannot be null"):
        load_runtime_config(null_strategy_data)

    bad_window = tmp_path / "bad-window.yaml"
    bad_window.write_text(nested.replace("lookback: 2}", "lookback: 2, typo: true}"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows\\[\\] unsupported keys: typo"):
        load_runtime_config(bad_window)

    null_windows = tmp_path / "null-windows.yaml"
    null_windows.write_text(nested.replace("windows:\n        - {name: fast, interval: 1m, lookback: 2}", "windows:"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows cannot be null"):
        load_runtime_config(null_windows)

    scalar_windows = tmp_path / "scalar-windows.yaml"
    scalar_windows.write_text(nested.replace("windows:\n        - {name: fast, interval: 1m, lookback: 2}", "windows: fast"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows must be a list"):
        load_runtime_config(scalar_windows)
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows must be a list"):
        RuntimeConfigLoader().from_mapping(
            {
                "mode": "replay",
                "data": {"adapter": "replay"},
                "broker": {"adapter": "paper"},
                "strategies": [
                    {
                        "name": "demo",
                        "class": "strategies.dummy.DummyStrategy",
                        "universe": ["AAA"],
                        "schedule": {"rebalance": "1m"},
                        "data": {"windows": ({"name": "fast", "interval": "1m", "lookback": 2},)},
                        "capital": {"mode": "notional", "amount": 10000},
                    }
                ],
            }
        )

    null_window = tmp_path / "null-window.yaml"
    null_window.write_text(nested.replace("- {name: fast, interval: 1m, lookback: 2}", "- null"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows\\[\\] cannot be null"):
        load_runtime_config(null_window)

    scalar_window = tmp_path / "scalar-window.yaml"
    scalar_window.write_text(nested.replace("- {name: fast, interval: 1m, lookback: 2}", "- fast"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows\\[\\] must be a mapping"):
        load_runtime_config(scalar_window)

    null_window_name = tmp_path / "null-window-name.yaml"
    null_window_name.write_text(nested.replace("name: fast", "name: null"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows\\[\\]\\.name cannot be null"):
        load_runtime_config(null_window_name)

    scalar_window_name = tmp_path / "scalar-window-name.yaml"
    scalar_window_name.write_text(nested.replace("name: fast", "name: 7"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows\\[\\]\\.name must be a string"):
        load_runtime_config(scalar_window_name)

    boolean_window_lookback = tmp_path / "boolean-window-lookback.yaml"
    boolean_window_lookback.write_text(nested.replace("lookback: 2", "lookback: true"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows\\[\\]\\.lookback must be an integer"):
        load_runtime_config(boolean_window_lookback)

    float_window_lookback = tmp_path / "float-window-lookback.yaml"
    float_window_lookback.write_text(nested.replace("lookback: 2", "lookback: 1.5"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.data\\.windows\\[\\]\\.lookback must be an integer"):
        load_runtime_config(float_window_lookback)

    bad_capital = tmp_path / "bad-capital.yaml"
    bad_capital.write_text(nested.replace("capital: {mode: notional, amount: 10000}", "capital: {mode: notional, amount: 10000, typo: true}"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.capital unsupported keys: typo"):
        load_runtime_config(bad_capital)

    null_capital = tmp_path / "null-capital.yaml"
    null_capital.write_text(nested.replace("capital: {mode: notional, amount: 10000}", "capital:"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.capital cannot be null"):
        load_runtime_config(null_capital)

    boolean_capital_amount = tmp_path / "boolean-capital-amount.yaml"
    boolean_capital_amount.write_text(nested.replace("amount: 10000", "amount: true"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.capital\\.amount must be a number"):
        load_runtime_config(boolean_capital_amount)

    scalar_capital_amount = tmp_path / "scalar-capital-amount.yaml"
    scalar_capital_amount.write_text(nested.replace("amount: 10000", "amount: nope"), encoding="utf-8")
    with pytest.raises(ValueError, match="strategies\\[\\]\\.capital\\.amount must be a number"):
        load_runtime_config(scalar_capital_amount)


def test_config_check_rejects_unknown_adapter_names(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: nope
broker:
  adapter: paper
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported data adapter: nope"):
        load_runtime_config(config)


def test_config_requires_runtime_mode_and_rejects_unsafe_mode_adapter_pairs(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    missing_mode = tmp_path / "missing-mode.yaml"
    missing_mode.write_text(
        """
data:
  adapter: replay
broker:
  adapter: paper
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="missing required key: mode"):
        load_runtime_config(missing_mode)

    live_replay = tmp_path / "live-replay.yaml"
    live_replay.write_text(
        missing_mode.read_text(encoding="utf-8").replace("data:", "mode: live\ndata:", 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="live mode requires non-replay data"):
        load_runtime_config(live_replay)

    sandbox_alpaca_paper = tmp_path / "sandbox-alpaca-paper.yaml"
    sandbox_alpaca_paper.write_text(
        """
mode: sandbox
data:
  adapter: ibkr
  host: 127.0.0.1
  port: 4002
  client_id: 7
broker:
  adapter: alpaca
  paper: true
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )
    loaded = load_runtime_config(sandbox_alpaca_paper)
    assert loaded.mode == "sandbox"


def test_config_check_requires_at_least_one_strategy(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
strategies: []
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least one strategy is required"):
        load_runtime_config(config)


def test_config_check_rejects_duplicate_strategy_names(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [BBB]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {BBB: 1.0}}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate strategy name: demo"):
        load_runtime_config(config)


def test_config_check_rejects_overlapping_strategy_universes(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
strategies:
  - name: alpha
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
  - name: beta
    class: strategies.dummy.DummyStrategy
    universe: [AAA, BBB]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {BBB: 1.0}}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="symbol AAA appears in multiple strategy universes"):
        load_runtime_config(config)


def test_root_risk_policy_is_strategy_default_until_overridden(tmp_path):
    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
  max_qty_per_order: 500
  max_notional_per_order: 10000
  max_gross_notional: 25000
  max_drawdown_pct: 5.0
  venue_rules:
    AAA: {longs_fractional_ok: true, shortable: false}
    BBB: {longs_fractional_ok: true, shortable: false}
strategies:
  - name: inherited
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
  - name: overridden
    class: strategies.dummy.DummyStrategy
    universe: [BBB]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    risk:
      max_notional_per_order: 20000
      venue_rules:
        BBB: {longs_fractional_ok: true, shortable: true}
    params: {weights: {BBB: 1.0}}
""",
    )

    inherited, overridden = load_runtime_config(config).strategies

    assert inherited.risk.max_qty_per_order == 500
    assert inherited.risk.max_notional_per_order == 10000
    assert inherited.risk.max_gross_notional == 25000
    assert inherited.risk.max_drawdown_pct == 5.0
    assert inherited.risk.venue_rules["AAA"].shortable is False
    assert overridden.risk.max_qty_per_order == 500
    assert overridden.risk.max_notional_per_order == 20000
    assert overridden.risk.max_gross_notional == 25000
    assert overridden.risk.max_drawdown_pct == 5.0
    assert overridden.risk.venue_rules["BBB"].shortable is True


def test_strategy_venue_rule_override_preserves_unspecified_root_fields(tmp_path):
    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
  venue_rules:
    aaa:
      longs_fractional_ok: false
      shortable: false
      lot_size: 5
      min_qty: 5
      min_notional: 100
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    risk:
      venue_rules:
        aaa: {max_notional_per_order: 5000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )

    rule = load_runtime_config(config).strategies[0].risk.venue_rules["AAA"]

    assert rule.longs_fractional_ok is False
    assert rule.shortable is False
    assert rule.lot_size == 5
    assert rule.min_qty == 5
    assert rule.min_notional == 100
    assert rule.max_notional_per_order == 5000


def test_venue_rule_aliases_reject_duplicate_normalized_symbols(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
  venue_rules:
    aaa: {shortable: false}
    AAA: {min_notional: 100}
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate venue rule symbol: AAA"):
        load_runtime_config(config)


def test_strategy_risk_override_rejects_explicit_null_guardrail(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
  max_notional_per_order: 10000
  venue_rules:
    AAA: {max_notional_per_order: 5000}
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    risk:
      max_notional_per_order:
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="risk.max_notional_per_order cannot be null"):
        load_runtime_config(config)

    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
  venue_rules:
    AAA: {max_notional_per_order: 5000}
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    risk:
      venue_rules:
        AAA: {max_notional_per_order: null}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="venue rule max_notional_per_order cannot be null"):
        load_runtime_config(config)

    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="risk cannot be null"):
        load_runtime_config(config)

    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
  venue_rules:
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="risk.venue_rules cannot be null"):
        load_runtime_config(config)

    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
  venue_rules: []
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="risk.venue_rules must be a mapping"):
        load_runtime_config(config)

    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
  venue_rules:
    AAA: []
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="risk.venue_rules.AAA must be a mapping"):
        load_runtime_config(config)

    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
risk:
  venue_rules:
    AAA:
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="risk.venue_rules.AAA cannot be null"):
        load_runtime_config(config)


def test_config_loader_rejects_string_booleans_for_recovery_adoption(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
mode: replay
data:
  adapter: replay
broker:
  adapter: paper
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {rebalance: 1m}
    data:
      windows:
        - {name: fast, interval: 1m, lookback: 2}
    capital: {mode: notional, amount: 10000}
    allow_adoption: "false"
    params: {weights: {AAA: 1.0}}
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="strategies\\[\\]\\.allow_adoption must be boolean"):
        load_runtime_config(config)


def test_ci_workflow_runs_design_acceptance_commands():
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "uv run --python 3.12 pytest -q" in workflow
    assert (
        "uv run --python 3.12 python -m compileall -q "
        "adapters domain observability runtime strategies tests cli.py main.py"
    ) in workflow
    assert "docker compose config --quiet" in workflow


def test_jsonl_events_and_status_health_are_machine_readable(tmp_path):
    from observability.events import JsonlEventSink
    from observability.health import check_result
    from observability.status import StatusWriter

    sink = JsonlEventSink(tmp_path / "events", run_id="run-1", mode="replay")
    sink.record("decision", {"strategy": "demo", "action": "hold"})
    sink.record("status", {"ready": True})
    sink.record("decision", {"strategy": "demo", "seq": 999, "run_id": "payload-run", "mode": "payload-mode"})

    rows = list((tmp_path / "events").glob("*.jsonl"))
    assert rows
    first, second, third = [json.loads(line) for line in rows[0].read_text().splitlines()]
    assert first["event"] == "decision"
    assert first["strategy"] == "demo"
    assert first["run_id"] == "run-1"
    assert first["mode"] == "replay"
    assert first["seq"] == 1
    assert second["event"] == "status"
    assert second["seq"] == 2
    assert third["run_id"] == "run-1"
    assert third["mode"] == "replay"
    assert third["seq"] == 3

    status_path = tmp_path / "status.json"
    StatusWriter(status_path, run_id="run-1", mode="replay").write(
        {
            "ready": True,
            "status": "running",
            "active_strategies": ["demo"],
            "run_id": "payload-run",
            "mode": "payload-mode",
        }
    )
    status_payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert status_payload["run_id"] == "run-1"
    assert status_payload["mode"] == "replay"
    assert check_result(status_path, required_strategies=("demo",)) == (0, "")


def test_replay_adapters_satisfy_ports_without_live_services():
    import asyncio

    from adapters.replay import ReplayBroker, ReplayMarketData
    from domain.market import DataRequest, Quote
    from domain.orders import OrderIntent
    from domain.portfolio import AccountSnapshot, BrokerSnapshot

    data = ReplayMarketData(
        warmup_rows={"demo:fast:1m:2": {"AAA": [24.0, 25.0]}},
        quotes=(Quote("AAA", 26.0, now=datetime.now(timezone.utc)),),
    )
    warm = asyncio.run(data.warmup((DataRequest("demo", "fast", ("AAA",), "1m", 2),)))
    assert warm["demo:fast:1m:2"]["AAA"] == [24.0, 25.0]

    broker = ReplayBroker(
        BrokerSnapshot(AccountSnapshot(100_000.0, 100_000.0), positions={}),
        execution_prices={"AAA": 25.0},
    )
    order = asyncio.run(broker.submit(OrderIntent("AAA", "buy", notional=1000.0)))
    assert order.status == "filled"
    assert asyncio.run(broker.list_open_orders(("AAA",))) == []


def test_replay_broker_prices_notional_fills_from_ground_truth_prices():
    import asyncio

    import pytest

    from adapters.replay import ReplayBroker
    from domain.orders import OrderIntent
    from domain.portfolio import AccountSnapshot, BrokerSnapshot

    unpriced = ReplayBroker(BrokerSnapshot(AccountSnapshot(100_000.0, 100_000.0), positions={}))
    with pytest.raises(ValueError, match="AAA: missing replay execution price"):
        asyncio.run(unpriced.submit(OrderIntent("AAA", "buy", notional=1000.0)))

    broker = ReplayBroker(
        BrokerSnapshot(AccountSnapshot(100_000.0, 100_000.0), positions={}),
        execution_prices={"AAA": 25.0},
    )
    order = asyncio.run(broker.submit(OrderIntent("AAA", "buy", notional=1000.0)))
    snapshot = asyncio.run(broker.snapshot())

    assert order.filled_qty == 40.0
    assert order.filled_avg_price == 25.0
    assert snapshot.positions["AAA"].qty == 40.0
