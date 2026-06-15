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
