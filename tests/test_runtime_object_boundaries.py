from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest


def _config_text(log_dir: str = "logs/events", status_path: str = "logs/status.json") -> str:
    return f"""
mode: replay
data:
  adapter: replay
  warmup_rows:
    demo:fast:1m:2:
      AAA: [24.0, 25.0]
  quotes:
    - {{symbol: AAA, price: 26.0}}
broker:
  adapter: paper
observability:
  log_dir: {log_dir}
  status_path: {status_path}
risk:
  max_gross_notional: 50000
strategies:
  - name: demo
    class: strategies.dummy.DummyStrategy
    universe: [AAA]
    schedule: {{rebalance: 1m}}
    data:
      windows:
        - {{name: fast, interval: 1m, lookback: 2}}
    capital: {{mode: notional, amount: 10000}}
    params:
      weights: {{AAA: 1.0}}
"""


def _spec():
    from domain.portfolio import RiskSpec, VenueRule
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec

    return StrategySpec(
        name="demo",
        class_path="strategies.dummy.DummyStrategy",
        universe=("AAA",),
        schedule=ScheduleSpec(rebalance="1m"),
        data=StrategyDataSpec(windows=(DataWindowSpec(name="fast", interval="1m", lookback=2),)),
        capital=CapitalSpec(amount=10_000.0),
        risk=RiskSpec(venue_rules={"AAA": VenueRule()}),
        params={"weights": {"AAA": 1.0}},
    )


def test_runtime_config_loader_owns_yaml_parsing(tmp_path):
    from runtime.config import RuntimeConfigLoader

    path = tmp_path / "config.yaml"
    path.write_text(_config_text(), encoding="utf-8")

    loaded = RuntimeConfigLoader().load(path)

    assert loaded.data.adapter == "replay"
    assert loaded.broker.adapter == "paper"
    assert loaded.strategies[0].name == "demo"
    assert loaded.strategies[0].capital.amount == 10_000.0


def test_adapter_factory_builds_replay_data_and_paper_broker():
    from adapters.replay import ReplayBroker, ReplayMarketData
    from runtime.config import AdapterConfig
    from runtime.factories import AdapterFactory

    factory = AdapterFactory()
    data = factory.build_data(
        AdapterConfig(
            "replay",
            {
                "warmup_rows": {"demo:fast:1m:2": {"AAA": [24.0, 25.0]}},
                "quotes": [{"symbol": "AAA", "price": 26.0}],
            },
        )
    )
    broker = factory.build_broker(AdapterConfig("paper", {}))

    assert isinstance(data, ReplayMarketData)
    assert isinstance(broker, ReplayBroker)


def test_adapter_factory_rejects_unknown_adapter_names():
    from runtime.config import AdapterConfig
    from runtime.factories import AdapterFactory

    with pytest.raises(ValueError, match="unsupported data adapter: nope"):
        AdapterFactory().build_data(AdapterConfig("nope", {}))


def test_adapter_factory_rejects_non_boolean_alpaca_paper_flag(monkeypatch):
    from runtime.config import AdapterConfig
    from runtime.factories import AdapterFactory

    def broker_kwargs(**kwargs):
        return kwargs

    monkeypatch.setattr("runtime.factories.AlpacaBroker", broker_kwargs)

    with pytest.raises(ValueError, match="broker.paper must be boolean"):
        AdapterFactory({"ALPACA_API_KEY": "key", "ALPACA_API_SECRET": "secret"}).build_broker(
            AdapterConfig("alpaca", {"paper": "false"})
        )


def test_ibkr_warmup_fetches_independent_windows_concurrently(async_gate):
    from adapters.data.ibkr import IBKRMarketData
    from domain.market import DataRequest

    class ConcurrentIBKR(IBKRMarketData):
        def __init__(self, gate):
            super().__init__(host="127.0.0.1", port=4002, client_id=1)
            self._ib = object()
            self.gate = gate

        async def _bars_for(self, request):
            await self.gate.enter(request.key)
            return {symbol: [1.0, 2.0] for symbol in request.symbols}

    async def run():
        gate = async_gate()
        feed = ConcurrentIBKR(gate)
        requests = (
            DataRequest("alpha", "fast", ("AAA",), "1m", 2),
            DataRequest("beta", "fast", ("BBB",), "1m", 2),
        )
        task = asyncio.create_task(feed.warmup(requests))
        await gate.wait_and_release()
        return gate, await task

    gate, rows = asyncio.run(run())

    assert gate.started == ["alpha:fast:1m:2", "beta:fast:1m:2"]
    assert rows["alpha:fast:1m:2"] == {"AAA": [1.0, 2.0]}
    assert rows["beta:fast:1m:2"] == {"BBB": [1.0, 2.0]}


def test_alpaca_broker_cancels_open_orders_concurrently(monkeypatch, async_gate):
    from adapters.broker.alpaca import AlpacaBroker
    from domain.orders import OrderState

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(cancel_order_by_id=lambda order_id: None)
    gate = async_gate()

    async def list_open_orders(symbols):
        return (
            OrderState("order-1", None, "AAA", "buy", "new"),
            OrderState("order-2", None, "BBB", "buy", "new"),
        )

    async def fake_to_thread(fn, order_id):
        await gate.enter(order_id)

    broker.list_open_orders = list_open_orders
    monkeypatch.setattr("adapters.broker.alpaca.asyncio.to_thread", fake_to_thread)

    async def run():
        task = asyncio.create_task(broker.cancel_open_orders(("AAA", "BBB")))
        await gate.wait_and_release()
        await task

    asyncio.run(run())

    assert gate.started == ["order-1", "order-2"]


def test_alpaca_broker_closes_positions_concurrently(monkeypatch, async_gate):
    from adapters.broker.alpaca import AlpacaBroker

    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._client = SimpleNamespace(close_position=lambda symbol_or_asset_id: None)
    gate = async_gate()

    async def fake_to_thread(fn, *, symbol_or_asset_id):
        await gate.enter(symbol_or_asset_id)
        return SimpleNamespace(
            id=f"close-{symbol_or_asset_id}",
            client_order_id=None,
            symbol=symbol_or_asset_id,
            side="sell",
            status="filled",
            filled_qty="1",
            filled_avg_price="25",
        )

    monkeypatch.setattr("adapters.broker.alpaca.asyncio.to_thread", fake_to_thread)

    async def run():
        task = asyncio.create_task(broker.close_positions(("AAA", "BBB")))
        await gate.wait_and_release()
        return await task

    orders = asyncio.run(run())

    assert gate.started == ["AAA", "BBB"]
    assert [order.symbol for order in orders] == ["AAA", "BBB"]


def test_strategy_factory_loads_configured_strategy_class():
    from runtime.factories import StrategyFactory
    from strategies.dummy import DummyStrategy

    strategy = StrategyFactory().load(_spec())

    assert isinstance(strategy, DummyStrategy)


def test_composition_root_builds_runtime_app_from_config(tmp_path):
    from runtime.app import RuntimeApp
    from runtime.composition import RuntimeCompositionRoot

    path = tmp_path / "config.yaml"
    path.write_text(
        _config_text(
            log_dir=str(tmp_path / "events"),
            status_path=str(tmp_path / "status.json"),
        ),
        encoding="utf-8",
    )

    app = RuntimeCompositionRoot().build_app(path)

    assert isinstance(app, RuntimeApp)


def test_registry_remains_a_compatibility_facade():
    from runtime import registry
    from runtime.config import AdapterConfig, RuntimeConfig, RuntimeConfigLoader
    from runtime.composition import RuntimeCompositionRoot
    from runtime.factories import AdapterFactory, StrategyFactory

    assert registry.AdapterConfig is AdapterConfig
    assert registry.RuntimeConfig is RuntimeConfig
    assert isinstance(registry.RuntimeConfigLoader(), RuntimeConfigLoader)
    assert isinstance(registry.AdapterFactory(), AdapterFactory)
    assert isinstance(registry.StrategyFactory(), StrategyFactory)
    assert isinstance(registry.RuntimeCompositionRoot(), RuntimeCompositionRoot)


def test_context_builder_returns_explicit_ready_and_blocked_variants():
    import numpy as np

    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, Position
    from runtime.context import ContextBlocked, ContextBuilder, ContextReady
    from runtime.data_hub import DataView
    from runtime.reasons import ReasonCode

    now = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)
    spec = _spec()
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=90_000.0),
        positions={"AAA": Position("AAA", qty=100.0)},
    )
    ready = DataView(
        strategy="demo",
        now=now,
        prices={"AAA": 25.0},
        bid_ask={"AAA": BidAsk(24.99, 25.01)},
        windows={"fast": np.array([[24.0], [25.0]])},
        fresh=True,
        ready=True,
    )
    blocked = DataView("demo", now, {}, {}, {}, fresh=False, ready=False, block_reason="missing_prices")
    unavailable = DataView("demo", now, {}, {}, {}, fresh=False, ready=False)

    assert isinstance(ContextBuilder().build(spec, ready, broker, date(2026, 6, 12), "rebalance"), ContextReady)
    assert isinstance(ContextBuilder().build(spec, blocked, broker, date(2026, 6, 12), "rebalance"), ContextBlocked)
    assert ContextBuilder().build(spec, unavailable, broker, date(2026, 6, 12), "rebalance").reason is ReasonCode.DATA_UNAVAILABLE


def test_risk_engine_returns_explicit_decision_variants():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position
    from runtime.data_hub import DataView
    from runtime.risk_engine import RiskAllowed, RiskBlocked, RiskEngine, RiskFlatten

    now = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=50_000.0),
        positions={"AAA": Position("AAA", qty=100.0)},
    )
    priced = DataView("demo", now, {"AAA": 25.0}, {}, {}, fresh=True, ready=True)
    stale = DataView("demo", now, {}, {}, {}, fresh=False, ready=False, block_reason="stale_quotes")

    engine = RiskEngine()

    assert isinstance(engine.check(_spec(), PortfolioTarget.hold("idle"), broker, priced), RiskAllowed)
    assert isinstance(engine.check(_spec(), PortfolioTarget.weights({"AAA": 1.0}, "entry"), broker, stale), RiskBlocked)
    assert isinstance(engine.check(_spec(), PortfolioTarget.hold("idle"), broker, stale), RiskFlatten)


def test_risk_engine_owns_an_ordered_rule_pipeline():
    from runtime.risk_engine import (
        DrawdownRule,
        FreshTargetDataRule,
        GrossNotionalRule,
        MaxOrderNotionalRule,
        MaxOrderQtyRule,
        MissingTargetPriceRule,
        RiskEngine,
        ShortSaleRule,
        TargetUniverseRule,
        UnpricedExposureRule,
        VenueMaxOrderNotionalRule,
    )

    engine = RiskEngine()

    assert engine.rules == (
        FreshTargetDataRule(),
        UnpricedExposureRule(),
        TargetUniverseRule(),
        ShortSaleRule(),
        MissingTargetPriceRule(),
        DrawdownRule(),
        GrossNotionalRule(),
        VenueMaxOrderNotionalRule(),
        MaxOrderNotionalRule(),
        MaxOrderQtyRule(),
    )


def test_risk_rules_are_independent_policy_objects():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position, RiskSpec, VenueRule
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec
    from runtime.data_hub import DataView
    from runtime.risk_engine import RiskAssessment, RiskFlatten, ShortSaleRule, UnpricedExposureRule

    spec = StrategySpec(
        name="demo",
        class_path="strategies.dummy.DummyStrategy",
        universe=("AAA",),
        schedule=ScheduleSpec("1m"),
        data=StrategyDataSpec((DataWindowSpec("fast", "1m", 2),)),
        capital=CapitalSpec(10_000.0),
        risk=RiskSpec(venue_rules={"AAA": VenueRule(shortable=False)}),
    )
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=50_000.0),
        positions={"AAA": Position("AAA", qty=100.0)},
    )
    data = DataView("demo", datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc), {}, {}, {}, False, False)
    assessment = RiskAssessment(spec, PortfolioTarget.weights({"AAA": -1.0}, "short"), broker, data)
    priced_data = DataView(
        "demo",
        datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
        {"AAA": 25.0},
        {},
        {},
        True,
        True,
    )
    priced_assessment = RiskAssessment(spec, PortfolioTarget.weights({"AAA": -1.0}, "short"), broker, priced_data)

    assert isinstance(UnpricedExposureRule().evaluate(assessment), RiskFlatten)
    assert ShortSaleRule().evaluate(priced_assessment).reason == "short_not_allowed"


def test_runtime_objects_use_domain_names_not_generic_manager_suffixes():
    import ast
    from pathlib import Path

    banned_suffixes = ("Manager", "Processor", "Computer")
    offenders = []
    for path in Path("runtime").glob("*.py"):
        tree = ast.parse(path.read_text())
        offenders.extend(
            f"{path}:{node.name}"
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.endswith(banned_suffixes)
        )

    assert offenders == []


def test_portfolio_engine_keeps_only_plan_line_and_engine_objects():
    import ast
    from pathlib import Path

    tree = ast.parse(Path("runtime/portfolio_engine.py").read_text())
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}

    assert classes == {"PortfolioLine", "PortfolioPlan", "PortfolioEngine"}


def test_portfolio_planning_objects_are_independent_units():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position, VenueRule
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec
    from runtime.portfolio_engine import PortfolioPlan

    spec = StrategySpec(
        name="demo",
        class_path="strategies.dummy.DummyStrategy",
        universe=("AAA", "BBB"),
        schedule=ScheduleSpec("1m"),
        data=StrategyDataSpec((DataWindowSpec("fast", "1m", 2),)),
        capital=CapitalSpec(10_000.0),
    )
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=100_000.0),
        positions={"AAA": Position("AAA", qty=100.0)},
    )
    plan = PortfolioPlan.from_target(
        spec,
        PortfolioTarget.weights({"AAA": -1.0, "BBB": 1.0}, "reverse"),
        broker,
        prices={"AAA": 25.0, "BBB": 50.0},
    )
    line = plan.line("AAA")
    intents = line.intents()

    assert plan.notionals == {"AAA": 5000.0, "BBB": 5000.0}
    assert plan.current_qty("AAA") == 100.0
    assert plan.target_qty("AAA") == -200.0
    assert line.rule == VenueRule(shortable=True)
    assert intents[0].side == "sell"
