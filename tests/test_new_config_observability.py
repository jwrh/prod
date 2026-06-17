from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def test_registry_loads_strict_config_and_rejects_unknown_keys(tmp_path, monkeypatch):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
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


def test_config_check_rejects_unknown_adapter_names(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
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


def test_config_check_requires_at_least_one_strategy(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
        """
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


def test_config_loader_rejects_string_booleans_for_recovery_adoption(tmp_path):
    import pytest

    from runtime.registry import load_runtime_config

    config = tmp_path / "config.yaml"
    config.write_text(
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

    sink = JsonlEventSink(tmp_path / "events")
    sink.record("decision", {"strategy": "demo", "action": "hold"})

    rows = list((tmp_path / "events").glob("*.jsonl"))
    assert rows
    payload = json.loads(rows[0].read_text().strip())
    assert payload["event"] == "decision"
    assert payload["strategy"] == "demo"

    status_path = tmp_path / "status.json"
    StatusWriter(status_path).write({"ready": True, "status": "running", "active_strategies": ["demo"]})
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
