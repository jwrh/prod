from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

import numpy as np


def _spec(name="demo", interval="1m"):
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec
    from domain.portfolio import RiskSpec, VenueRule

    return StrategySpec(
        name=name,
        class_path="tests.fake.Demo",
        universe=("AAA", "BBB"),
        schedule=ScheduleSpec(rebalance=interval),
        data=StrategyDataSpec(windows=(DataWindowSpec(name="fast", interval="1m", lookback=2),)),
        capital=CapitalSpec(amount=10_000.0),
        risk=RiskSpec(
            max_qty_per_order=1_000,
            max_notional_per_order=20_000.0,
            max_gross_notional=50_000.0,
            venue_rules={
                "AAA": VenueRule(longs_fractional_ok=True, shortable=True),
                "BBB": VenueRule(longs_fractional_ok=True, shortable=True),
            },
        ),
    )


def _risk_spec(**overrides):
    from domain.portfolio import RiskSpec, VenueRule

    values = {
        "max_qty_per_order": 1_000,
        "max_notional_per_order": 20_000.0,
        "max_gross_notional": 50_000.0,
        "max_drawdown_pct": None,
        "venue_rules": {
            "AAA": VenueRule(longs_fractional_ok=True, shortable=True),
            "BBB": VenueRule(longs_fractional_ok=True, shortable=True),
        },
    }
    values.update(overrides)
    return RiskSpec(**values)


def _spec_with_risk(risk):
    from dataclasses import replace

    return replace(_spec(), risk=risk)


def test_scheduler_emits_due_strategy_ticks_without_global_god_loop():
    from runtime.scheduler import RuntimeScheduler

    scheduler = RuntimeScheduler((_spec("fast", "1m"), _spec("daily", "1d")))
    now = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)

    first = scheduler.due_ticks(now)
    second = scheduler.due_ticks(now.replace(minute=31))

    assert [tick.strategy_name for tick in first] == ["fast", "daily"]
    assert [tick.strategy_name for tick in second] == ["fast"]
    assert first[0].trigger == "rebalance"


def test_data_hub_owns_warmup_quote_fan_in_and_strategy_views():
    from domain.market import Quote
    from runtime.data_hub import DataHub

    class Feed:
        async def connect(self): ...
        async def disconnect(self): ...
        async def warmup(self, requests):
            return {
                request.key: {
                    "AAA": [24.0, 25.0],
                    "BBB": [29.0, 30.0],
                }
                for request in requests
            }
        async def subscribe(self, symbols, sink):
            self.symbols = symbols
            self.sink = sink

    hub = DataHub(Feed(), quote_ttl_seconds=60.0)
    now = datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)
    asyncio.run(hub.warmup((_spec(),)))
    hub.on_quote(Quote("AAA", 26.0, now=now, bid=25.99, ask=26.01))
    hub.on_quote(Quote("BBB", 31.0, now=now, bid=30.99, ask=31.01))

    view = hub.snapshot("demo", now)

    assert view.ready is True
    assert view.prices == {"AAA": 26.0, "BBB": 31.0}
    assert view.windows["fast"].shape == (2, 2)
    assert view.bid_ask["AAA"].ask == 26.01


def test_data_hub_updates_forming_bar_without_destroying_interval_window():
    import asyncio

    from domain.market import Quote
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec
    from runtime.data_hub import DataHub

    spec = StrategySpec(
        name="bars",
        class_path="tests.fake.Demo",
        universe=("AAA",),
        schedule=ScheduleSpec(rebalance="1m"),
        data=StrategyDataSpec(windows=(DataWindowSpec(name="fast", interval="1m", lookback=3),)),
        capital=CapitalSpec(amount=10_000.0),
    )

    class Feed:
        async def connect(self): ...
        async def disconnect(self): ...
        async def warmup(self, requests):
            return {request.key: {"AAA": [100.0, 101.0, 102.0]} for request in requests}
        async def subscribe(self, symbols, sink):
            self.sink = sink

    hub = DataHub(Feed(), quote_ttl_seconds=60.0)
    asyncio.run(hub.warmup((spec,)))

    at_1430 = datetime(2026, 6, 12, 14, 30, 5, tzinfo=timezone.utc)
    hub.on_quote(Quote("AAA", 103.0, now=at_1430))
    hub.on_quote(Quote("AAA", 104.0, now=at_1430.replace(second=20)))
    same_bucket = hub.snapshot("bars", at_1430.replace(second=20)).windows["fast"]

    hub.on_quote(Quote("AAA", 105.0, now=at_1430.replace(minute=31, second=1)))
    next_bucket = hub.snapshot("bars", at_1430.replace(minute=31, second=1)).windows["fast"]

    assert same_bucket.tolist() == [[100.0], [101.0], [104.0]]
    assert next_bucket.tolist() == [[101.0], [104.0], [105.0]]


def test_context_computes_strategy_weights_from_broker_truth():
    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, Position
    from runtime.context import ContextBuilder, ContextReady
    from runtime.data_hub import DataView

    spec = _spec()
    data = DataView(
        strategy="demo",
        now=datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
        prices={"AAA": 25.0, "BBB": 50.0},
        bid_ask={"AAA": BidAsk(24.99, 25.01), "BBB": BidAsk(49.99, 50.01)},
        windows={"fast": np.array([[24.0, 49.0], [25.0, 50.0]])},
        fresh=True,
        ready=True,
    )
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=90_000.0),
        positions={"AAA": Position("AAA", qty=100.0), "BBB": Position("BBB", qty=-10.0)},
    )

    result = ContextBuilder().build(spec, data, broker, date(2026, 6, 12), "rebalance")

    assert isinstance(result, ContextReady)
    assert result.context.current_weights == {"AAA": 0.25, "BBB": -0.05}


def test_portfolio_engine_diffs_rebalance_reverse_and_flat_targets():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position
    from runtime.portfolio_engine import PortfolioEngine

    spec = _spec()
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=100_000.0),
        positions={"AAA": Position("AAA", qty=100.0), "BBB": Position("BBB", qty=0.0)},
    )
    engine = PortfolioEngine()

    reverse = engine.diff(
        spec,
        PortfolioTarget.weights({"AAA": -1.0, "BBB": 1.0}, "reverse"),
        broker,
        prices={"AAA": 25.0, "BBB": 50.0},
    )
    flat = engine.diff(spec, PortfolioTarget.flat("eod"), broker, prices={"AAA": 25.0, "BBB": 50.0})

    assert [(order.symbol, order.side) for order in reverse] == [("AAA", "sell"), ("BBB", "buy")]
    assert flat[0].symbol == "AAA"
    assert flat[0].side == "sell"


def test_portfolio_engine_intents_are_deterministic_for_same_plan():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget
    from runtime.portfolio_engine import PortfolioEngine

    spec = _spec()
    broker = BrokerSnapshot(account=AccountSnapshot(equity=100_000.0, cash=100_000.0), positions={})
    engine = PortfolioEngine()

    first = engine.diff(
        spec,
        PortfolioTarget.weights({"AAA": 0.6, "BBB": 0.4}, "entry"),
        broker,
        prices={"AAA": 25.0, "BBB": 50.0},
    )
    second = engine.diff(
        spec,
        PortfolioTarget.weights({"AAA": 0.6, "BBB": 0.4}, "entry"),
        broker,
        prices={"AAA": 25.0, "BBB": 50.0},
    )

    assert [order.client_order_id for order in first] == [order.client_order_id for order in second]


def test_fractional_long_positions_can_be_trimmed_without_residuals():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position
    from runtime.portfolio_engine import PortfolioEngine

    spec = _spec()
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=100_000.0),
        positions={"AAA": Position("AAA", qty=10.4)},
    )

    intents = PortfolioEngine().diff(spec, PortfolioTarget.flat("exit"), broker, prices={"AAA": 25.0, "BBB": 50.0})

    assert [(order.symbol, order.side, order.qty) for order in intents] == [("AAA", "sell", 10.4)]


def test_risk_engine_blocks_stale_data_and_forces_flat_on_missing_position_price():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position
    from runtime.data_hub import DataView
    from runtime.risk_engine import RiskBlocked, RiskEngine, RiskFlatten

    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=50_000.0),
        positions={"AAA": Position("AAA", qty=100.0)},
    )
    stale = DataView(
        strategy="demo",
        now=datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
        prices={},
        bid_ask={},
        windows={},
        fresh=False,
        ready=False,
        block_reason="stale_quotes",
    )

    decision = RiskEngine().check(_spec(), PortfolioTarget.weights({"AAA": 1.0}, "entry"), broker, stale)
    forced = RiskEngine().check(_spec(), PortfolioTarget.hold("idle"), broker, stale)

    assert isinstance(decision, RiskBlocked)
    assert decision.reason == "stale_quotes"
    assert isinstance(forced, RiskFlatten)
    assert forced.reason == "missing_position_price"


def test_risk_engine_uses_deployed_plan_not_raw_weight_magnitude_for_gross_limit():
    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget
    from runtime.data_hub import DataView
    from runtime.risk_engine import RiskBlocked, RiskEngine

    spec = _spec_with_risk(_risk_spec(max_gross_notional=9_999.0))
    broker = BrokerSnapshot(account=AccountSnapshot(equity=100_000.0, cash=100_000.0), positions={})
    data = DataView(
        strategy="demo",
        now=datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
        prices={"AAA": 25.0, "BBB": 50.0},
        bid_ask={"AAA": BidAsk(24.99, 25.01), "BBB": BidAsk(49.99, 50.01)},
        windows={},
        fresh=True,
        ready=True,
    )

    decision = RiskEngine().check(spec, PortfolioTarget.weights({"AAA": 0.1}, "entry"), broker, data)

    assert isinstance(decision, RiskBlocked)
    assert decision.reason == "max_gross_notional"


def test_risk_engine_blocks_orders_over_configured_notional_or_quantity_limits():
    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget
    from runtime.data_hub import DataView
    from runtime.risk_engine import RiskBlocked, RiskEngine

    broker = BrokerSnapshot(account=AccountSnapshot(equity=100_000.0, cash=100_000.0), positions={})
    data = DataView(
        strategy="demo",
        now=datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
        prices={"AAA": 25.0, "BBB": 50.0},
        bid_ask={"AAA": BidAsk(24.99, 25.01), "BBB": BidAsk(49.99, 50.01)},
        windows={},
        fresh=True,
        ready=True,
    )

    over_notional = RiskEngine().check(
        _spec_with_risk(_risk_spec(max_notional_per_order=9_999.0)),
        PortfolioTarget.weights({"AAA": 0.1}, "entry"),
        broker,
        data,
    )
    over_qty = RiskEngine().check(
        _spec_with_risk(_risk_spec(max_qty_per_order=399.0)),
        PortfolioTarget.weights({"AAA": 0.1}, "entry"),
        broker,
        data,
    )

    assert isinstance(over_notional, RiskBlocked)
    assert over_notional.reason == "max_notional_per_order"
    assert isinstance(over_qty, RiskBlocked)
    assert over_qty.reason == "max_qty_per_order"


def test_risk_engine_flattens_after_configured_drawdown_from_high_watermark():
    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position
    from runtime.data_hub import DataView
    from runtime.risk_engine import RiskEngine, RiskFlatten

    spec = _spec_with_risk(_risk_spec(max_drawdown_pct=5.0))
    engine = RiskEngine()

    def data_view():
        return DataView(
            strategy="demo",
            now=datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
            prices={"AAA": 25.0, "BBB": 50.0},
            bid_ask={"AAA": BidAsk(24.99, 25.01), "BBB": BidAsk(49.99, 50.01)},
            windows={},
            fresh=True,
            ready=True,
        )

    engine.check(
        spec,
        PortfolioTarget.hold("idle"),
        BrokerSnapshot(AccountSnapshot(equity=100_000.0, cash=100_000.0), positions={}),
        data_view(),
    )
    decision = engine.check(
        spec,
        PortfolioTarget.weights({"AAA": 1.0}, "entry"),
        BrokerSnapshot(
            AccountSnapshot(equity=94_000.0, cash=94_000.0),
            positions={"AAA": Position("AAA", qty=1.0)},
        ),
        data_view(),
    )

    assert isinstance(decision, RiskFlatten)
    assert decision.reason == "max_drawdown_pct"
