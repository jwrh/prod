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


def test_data_hub_rejects_short_warmup_history_before_trading():
    import pytest

    from runtime.data_hub import DataHub

    class Feed:
        async def connect(self): ...
        async def disconnect(self): ...
        async def warmup(self, requests):
            return {request.key: {"AAA": [25.0], "BBB": [30.0]} for request in requests}
        async def subscribe(self, symbols, sink):
            raise AssertionError("short warmup history must not subscribe")

    hub = DataHub(Feed(), quote_ttl_seconds=60.0)

    with pytest.raises(ValueError, match="demo:fast: expected 2 warmup rows, got 1"):
        asyncio.run(hub.warmup((_spec(),)))


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


def test_data_hub_blocks_out_of_sequence_quotes_until_newer_quote():
    from domain.market import Quote
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec
    from runtime.data_hub import DataHub

    spec = StrategySpec(
        name="sequence",
        class_path="tests.fake.Demo",
        universe=("AAA",),
        schedule=ScheduleSpec(rebalance="1m"),
        data=StrategyDataSpec(windows=(DataWindowSpec(name="fast", interval="1m", lookback=2),)),
        capital=CapitalSpec(amount=10_000.0),
    )

    class Feed:
        async def connect(self): ...
        async def disconnect(self): ...
        async def warmup(self, requests):
            return {request.key: {"AAA": [100.0, 101.0]} for request in requests}
        async def subscribe(self, symbols, sink):
            self.sink = sink

    hub = DataHub(Feed(), quote_ttl_seconds=60.0)
    now = datetime(2026, 6, 12, 14, 30, 30, tzinfo=timezone.utc)
    asyncio.run(hub.warmup((spec,)))
    hub.on_quote(Quote("AAA", 102.0, now=now))

    ready = hub.snapshot("sequence", now)
    assert ready.ready is True

    hub.on_quote(Quote("AAA", 101.0, now=now.replace(second=0)))
    blocked = hub.snapshot("sequence", now)

    assert blocked.ready is False
    assert blocked.block_reason == "out_of_sequence_data"

    hub.on_quote(Quote("AAA", 103.0, now=now.replace(second=31)))
    restored = hub.snapshot("sequence", now.replace(second=31))

    assert restored.ready is True
    assert restored.prices == {"AAA": 103.0}


def test_data_hub_keeps_sequence_block_until_timestamp_advances():
    from domain.market import Quote
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec
    from runtime.data_hub import DataHub

    spec = StrategySpec(
        name="sequence",
        class_path="tests.fake.Demo",
        universe=("AAA",),
        schedule=ScheduleSpec(rebalance="1m"),
        data=StrategyDataSpec(windows=(DataWindowSpec(name="fast", interval="1m", lookback=2),)),
        capital=CapitalSpec(amount=10_000.0),
    )

    class Feed:
        async def connect(self): ...
        async def disconnect(self): ...
        async def warmup(self, requests):
            return {request.key: {"AAA": [100.0, 101.0]} for request in requests}
        async def subscribe(self, symbols, sink):
            self.sink = sink

    hub = DataHub(Feed(), quote_ttl_seconds=60.0)
    now = datetime(2026, 6, 12, 14, 30, 30, tzinfo=timezone.utc)
    asyncio.run(hub.warmup((spec,)))
    hub.on_quote(Quote("AAA", 102.0, now=now))
    hub.on_quote(Quote("AAA", 101.0, now=now.replace(second=0)))
    hub.on_quote(Quote("AAA", 102.5, now=now))

    blocked = hub.snapshot("sequence", now)
    assert blocked.ready is False
    assert blocked.block_reason == "out_of_sequence_data"

    hub.on_quote(Quote("AAA", 103.0, now=now.replace(second=31)))
    restored = hub.snapshot("sequence", now.replace(second=31))
    assert restored.ready is True
    assert restored.prices == {"AAA": 103.0}


def test_data_hub_freezes_shared_windows_while_symbol_is_sequence_blocked():
    from domain.market import Quote
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec
    from runtime.data_hub import DataHub

    spec = StrategySpec(
        name="sequence",
        class_path="tests.fake.Demo",
        universe=("AAA", "BBB"),
        schedule=ScheduleSpec(rebalance="1m"),
        data=StrategyDataSpec(windows=(DataWindowSpec(name="fast", interval="1m", lookback=2),)),
        capital=CapitalSpec(amount=10_000.0),
    )

    class Feed:
        async def connect(self): ...
        async def disconnect(self): ...
        async def warmup(self, requests):
            return {request.key: {"AAA": [100.0, 101.0], "BBB": [50.0, 51.0]} for request in requests}
        async def subscribe(self, symbols, sink):
            self.sink = sink

    hub = DataHub(Feed(), quote_ttl_seconds=60.0)
    now = datetime(2026, 6, 12, 14, 30, 30, tzinfo=timezone.utc)
    asyncio.run(hub.warmup((spec,)))
    hub.on_quote(Quote("AAA", 102.0, now=now))
    hub.on_quote(Quote("BBB", 52.0, now=now))
    before_block = hub.snapshot("sequence", now).windows["fast"]

    hub.on_quote(Quote("AAA", 101.0, now=now.replace(second=0)))
    hub.on_quote(Quote("BBB", 53.0, now=now.replace(minute=31)))
    hub.on_quote(Quote("AAA", 104.0, now=now.replace(minute=32, second=1)))
    restored = hub.snapshot("sequence", now.replace(minute=32, second=1))

    assert restored.ready is True
    assert before_block.tolist() == [[100.0, 50.0], [102.0, 52.0]]
    assert restored.windows["fast"].tolist() == [[102.0, 52.0], [104.0, 53.0]]


def test_data_hub_resets_quote_sequence_on_new_warmup_epoch():
    from domain.market import Quote
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec
    from runtime.data_hub import DataHub

    spec = StrategySpec(
        name="sequence",
        class_path="tests.fake.Demo",
        universe=("AAA",),
        schedule=ScheduleSpec(rebalance="1m"),
        data=StrategyDataSpec(windows=(DataWindowSpec(name="fast", interval="1m", lookback=2),)),
        capital=CapitalSpec(amount=10_000.0),
    )

    class Feed:
        async def connect(self): ...
        async def disconnect(self): ...
        async def warmup(self, requests):
            return {request.key: {"AAA": [100.0, 101.0]} for request in requests}
        async def subscribe(self, symbols, sink):
            self.sink = sink

    hub = DataHub(Feed(), quote_ttl_seconds=60.0)
    old_epoch = datetime(2026, 6, 12, 14, 30, 30, tzinfo=timezone.utc)
    new_epoch = old_epoch.replace(second=0)
    asyncio.run(hub.warmup((spec,)))
    hub.on_quote(Quote("AAA", 102.0, now=old_epoch))

    asyncio.run(hub.warmup((spec,)))
    hub.on_quote(Quote("AAA", 101.0, now=new_epoch))
    restored = hub.snapshot("sequence", new_epoch)

    assert restored.ready is True
    assert restored.prices == {"AAA": 101.0}


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


def test_portfolio_engine_uses_price_grounded_qty_for_fractional_long_entries():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget
    from runtime.portfolio_engine import PortfolioEngine

    spec = _spec()
    broker = BrokerSnapshot(account=AccountSnapshot(equity=100_000.0, cash=100_000.0), positions={})

    [intent] = PortfolioEngine().diff(
        spec,
        PortfolioTarget.weights({"AAA": 1.0}, "entry"),
        broker,
        prices={"AAA": 25.0, "BBB": 50.0},
    )

    assert intent.symbol == "AAA"
    assert intent.side == "buy"
    assert intent.qty == 400.0
    assert intent.notional is None


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


def test_fractional_exits_truncate_without_crossing_flat():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position
    from runtime.portfolio_engine import PortfolioEngine

    spec = _spec()
    engine = PortfolioEngine()
    account = AccountSnapshot(equity=100_000.0, cash=100_000.0)
    prices = {"AAA": 25.0, "BBB": 50.0}

    long_broker = BrokerSnapshot(account=account, positions={"AAA": Position("AAA", qty=1.1234567)})
    [long_exit] = engine.diff(spec, PortfolioTarget.flat("exit_long"), long_broker, prices=prices)

    short_broker = BrokerSnapshot(account=account, positions={"AAA": Position("AAA", qty=-1.1234567)})
    [short_exit] = engine.diff(spec, PortfolioTarget.flat("exit_short"), short_broker, prices=prices)

    assert (long_exit.side, long_exit.qty) == ("sell", 1.123456)
    assert (short_exit.side, short_exit.qty) == ("buy", 1.123456)


def test_fractional_long_exit_below_min_qty_still_flattens():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position
    from runtime.portfolio_engine import PortfolioEngine

    spec = _spec()
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=100_000.0),
        positions={"AAA": Position("AAA", qty=0.5)},
    )

    [intent] = PortfolioEngine().diff(spec, PortfolioTarget.flat("exit"), broker, prices={"AAA": 25.0, "BBB": 50.0})

    assert (intent.side, intent.qty) == ("sell", 0.5)


def test_portfolio_engine_uses_symbol_min_notional_for_fractional_buys():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position, RiskSpec, VenueRule
    from runtime.portfolio_engine import PortfolioEngine

    spec = _spec_with_risk(
        RiskSpec(
            venue_rules={
                "AAA": VenueRule(longs_fractional_ok=True, shortable=True, min_notional=50.0),
                "BBB": VenueRule(longs_fractional_ok=True, shortable=True),
            },
        )
    )
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=100_000.0),
        positions={"AAA": Position("AAA", qty=399.0)},
    )

    intents = PortfolioEngine().diff(
        spec,
        PortfolioTarget.weights({"AAA": 1.0}, "small_top_up"),
        broker,
        prices={"AAA": 25.0, "BBB": 50.0},
    )

    assert intents == []


def test_portfolio_engine_uses_symbol_min_notional_for_whole_share_buys():
    from dataclasses import replace

    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, RiskSpec, VenueRule
    from domain.strategy import CapitalSpec
    from runtime.portfolio_engine import PortfolioEngine

    spec = replace(
        _spec_with_risk(
            RiskSpec(
                venue_rules={"AAA": VenueRule(longs_fractional_ok=False, shortable=True, min_notional=50.0)}
            )
        ),
        universe=("AAA",),
        capital=CapitalSpec(amount=25.0),
    )
    broker = BrokerSnapshot(account=AccountSnapshot(equity=1_000.0, cash=1_000.0), positions={})

    intents = PortfolioEngine().diff(spec, PortfolioTarget.weights({"AAA": 1.0}, "small_entry"), broker, prices={"AAA": 25.0})

    assert intents == []


def test_portfolio_engine_uses_rounded_entry_notional_for_min_notional():
    from dataclasses import replace

    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position, RiskSpec, VenueRule
    from domain.strategy import CapitalSpec
    from runtime.portfolio_engine import PortfolioEngine

    spec = replace(
        _spec_with_risk(
            RiskSpec(
                venue_rules={"AAA": VenueRule(longs_fractional_ok=False, shortable=True, min_notional=50.0)}
            )
        ),
        universe=("AAA",),
        capital=CapitalSpec(amount=51.0),
    )
    engine = PortfolioEngine()
    prices = {"AAA": 26.0}

    flat = BrokerSnapshot(account=AccountSnapshot(equity=1_000.0, cash=1_000.0), positions={})
    assert engine.diff(spec, PortfolioTarget.weights({"AAA": 1.0}, "rounded_long"), flat, prices=prices) == []
    assert engine.diff(spec, PortfolioTarget.weights({"AAA": -1.0}, "rounded_short"), flat, prices=prices) == []

    long_broker = BrokerSnapshot(
        account=AccountSnapshot(equity=1_000.0, cash=1_000.0),
        positions={"AAA": Position("AAA", qty=10.0)},
    )
    assert engine.diff(spec, PortfolioTarget.weights({"AAA": -1.0}, "rounded_flip_short"), long_broker, prices=prices) == []

    short_broker = BrokerSnapshot(
        account=AccountSnapshot(equity=1_000.0, cash=1_000.0),
        positions={"AAA": Position("AAA", qty=-10.0)},
    )
    assert engine.diff(spec, PortfolioTarget.weights({"AAA": 1.0}, "rounded_flip_long"), short_broker, prices=prices) == []


def test_portfolio_engine_uses_symbol_min_notional_for_short_entries():
    from dataclasses import replace

    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, RiskSpec, VenueRule
    from domain.strategy import CapitalSpec
    from runtime.portfolio_engine import PortfolioEngine

    spec = replace(
        _spec_with_risk(
            RiskSpec(
                venue_rules={"AAA": VenueRule(longs_fractional_ok=True, shortable=True, min_notional=50.0)}
            )
        ),
        universe=("AAA",),
        capital=CapitalSpec(amount=25.0),
    )
    broker = BrokerSnapshot(account=AccountSnapshot(equity=1_000.0, cash=1_000.0), positions={})

    intents = PortfolioEngine().diff(spec, PortfolioTarget.weights({"AAA": -1.0}, "small_short"), broker, prices={"AAA": 25.0})

    assert intents == []


def test_portfolio_engine_uses_min_notional_for_zero_crossing_entry_exposure():
    from dataclasses import replace

    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position, RiskSpec, VenueRule
    from domain.strategy import CapitalSpec
    from runtime.portfolio_engine import PortfolioEngine

    spec = replace(
        _spec_with_risk(
            RiskSpec(
                venue_rules={"AAA": VenueRule(longs_fractional_ok=True, shortable=True, min_notional=50.0)}
            )
        ),
        universe=("AAA",),
        capital=CapitalSpec(amount=25.0),
    )
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=1_000.0, cash=1_000.0),
        positions={"AAA": Position("AAA", qty=10.0)},
    )

    intents = PortfolioEngine().diff(
        spec,
        PortfolioTarget.weights({"AAA": -1.0}, "small_flip_short"),
        broker,
        prices={"AAA": 25.0},
    )

    assert intents == []

    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=1_000.0, cash=1_000.0),
        positions={"AAA": Position("AAA", qty=-10.0)},
    )
    intents = PortfolioEngine().diff(
        spec,
        PortfolioTarget.weights({"AAA": 1.0}, "small_flip_long"),
        broker,
        prices={"AAA": 25.0},
    )

    assert intents == []


def test_portfolio_engine_allows_small_buy_to_cover_below_min_notional():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position, RiskSpec, VenueRule
    from runtime.portfolio_engine import PortfolioEngine

    spec = _spec_with_risk(
        RiskSpec(
            venue_rules={
                "AAA": VenueRule(longs_fractional_ok=True, shortable=True, min_notional=50.0),
                "BBB": VenueRule(longs_fractional_ok=True, shortable=True),
            },
        )
    )
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=1_000.0, cash=1_000.0),
        positions={"AAA": Position("AAA", qty=-1.0)},
    )

    intents = PortfolioEngine().diff(spec, PortfolioTarget.flat("cover"), broker, prices={"AAA": 25.0, "BBB": 50.0})

    assert [(order.symbol, order.side, order.qty) for order in intents] == [("AAA", "buy", 1.0)]


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


def test_risk_engine_blocks_symbol_max_notional_per_order():
    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, RiskSpec, VenueRule
    from runtime.data_hub import DataView
    from runtime.risk_engine import RiskBlocked, RiskEngine

    spec = _spec_with_risk(
        RiskSpec(
            max_notional_per_order=20_000.0,
            max_gross_notional=50_000.0,
            venue_rules={
                "AAA": VenueRule(longs_fractional_ok=True, shortable=True, max_notional_per_order=5_000.0),
                "BBB": VenueRule(longs_fractional_ok=True, shortable=True),
            },
        )
    )
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

    decision = RiskEngine().check(spec, PortfolioTarget.weights({"AAA": 1.0}, "entry"), broker, data)

    assert isinstance(decision, RiskBlocked)
    assert decision.reason == "max_notional_per_order"


def test_risk_engine_blocks_zero_crossing_order_over_symbol_max_notional():
    from dataclasses import replace

    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position, RiskSpec, VenueRule
    from domain.strategy import CapitalSpec
    from runtime.data_hub import DataView
    from runtime.risk_engine import RiskBlocked, RiskEngine

    spec = replace(
        _spec_with_risk(
            RiskSpec(venue_rules={"AAA": VenueRule(shortable=True, max_notional_per_order=60.0)})
        ),
        universe=("AAA",),
        capital=CapitalSpec(amount=25.0),
    )
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=100_000.0),
        positions={"AAA": Position("AAA", qty=10.0)},
    )
    data = DataView(
        strategy="demo",
        now=datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
        prices={"AAA": 25.0},
        bid_ask={"AAA": BidAsk(24.99, 25.01)},
        windows={},
        fresh=True,
        ready=True,
    )

    decision = RiskEngine().check(spec, PortfolioTarget.weights({"AAA": -1.0}, "flip"), broker, data)

    assert isinstance(decision, RiskBlocked)
    assert decision.reason == "max_notional_per_order"


def test_risk_engine_allows_unshortable_symbol_when_rounding_only_flattens_long():
    from dataclasses import replace

    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position, RiskSpec, VenueRule
    from domain.strategy import CapitalSpec
    from runtime.data_hub import DataView
    from runtime.risk_engine import RiskAllowed, RiskEngine

    spec = replace(
        _spec_with_risk(
            RiskSpec(venue_rules={"AAA": VenueRule(longs_fractional_ok=False, shortable=False)})
        ),
        universe=("AAA",),
        capital=CapitalSpec(amount=2.6),
    )
    broker = BrokerSnapshot(
        account=AccountSnapshot(equity=100_000.0, cash=100_000.0),
        positions={"AAA": Position("AAA", qty=10.0)},
    )
    data = DataView(
        strategy="demo",
        now=datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
        prices={"AAA": 26.0},
        bid_ask={"AAA": BidAsk(25.99, 26.01)},
        windows={},
        fresh=True,
        ready=True,
    )

    decision = RiskEngine().check(spec, PortfolioTarget.weights({"AAA": -1.0}, "rounded_flat"), broker, data)

    assert isinstance(decision, RiskAllowed)


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
