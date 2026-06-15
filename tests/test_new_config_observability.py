from __future__ import annotations

import json
from datetime import datetime, timezone


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

    broker = ReplayBroker(BrokerSnapshot(AccountSnapshot(100_000.0, 100_000.0), positions={}))
    order = asyncio.run(broker.submit(OrderIntent("AAA", "buy", notional=1000.0)))
    assert order.status == "filled"
    assert asyncio.run(broker.list_open_orders(("AAA",))) == []
