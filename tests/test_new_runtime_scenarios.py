from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone


def _spec():
    from domain.portfolio import RiskSpec, VenueRule
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec

    return StrategySpec(
        name="demo",
        class_path="tests.fake.Demo",
        universe=("AAA",),
        schedule=ScheduleSpec(rebalance="1m"),
        data=StrategyDataSpec(windows=(DataWindowSpec(name="fast", interval="1m", lookback=2),)),
        capital=CapitalSpec(amount=10_000.0),
        risk=RiskSpec(venue_rules={"AAA": VenueRule()}),
    )


class EventRecorder:
    def __init__(self):
        self.events = []

    def record(self, event_type, payload):
        self.events.append((event_type, payload))


class StatusRecorder:
    def __init__(self):
        self.rows = []

    def write(self, payload):
        self.rows.append(payload)


class Strategy:
    def __init__(self, target):
        self.target = target
        self.contexts = []

    def evaluate(self, ctx):
        self.contexts.append(ctx)
        return self.target


class Broker:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.submitted = []
        self.cancel_calls = []
        self.closed = []

    async def snapshot(self):
        return self._snapshot

    async def list_open_orders(self, symbols):
        return []

    async def cancel_open_orders(self, symbols):
        self.cancel_calls.append(tuple(symbols))

    async def close_positions(self, symbols):
        self.closed.extend(symbols)
        return []

    async def submit(self, order):
        from domain.orders import OrderState

        self.submitted.append(order)
        return OrderState(
            id=f"order-{len(self.submitted)}",
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            status="filled",
            filled_qty=order.qty or 1.0,
            filled_avg_price=25.0,
        )

    async def get_order(self, order_id):
        return None


def _data_view(*, ready=True, fresh=True):
    import numpy as np
    from domain.market import BidAsk
    from runtime.data_hub import DataView

    return DataView(
        strategy="demo",
        now=datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
        prices={"AAA": 25.0} if ready else {},
        bid_ask={"AAA": BidAsk(24.99, 25.01)} if ready else {},
        windows={"fast": np.array([[24.0], [25.0]])} if ready else {},
        fresh=fresh,
        ready=ready,
        block_reason=None if ready else "missing_prices",
    )


def test_supervisor_coordinates_a_rebalance_tick_end_to_end():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget
    from runtime.context import ContextBuilder
    from runtime.execution_engine import ExecutionEngine
    from runtime.portfolio_engine import PortfolioEngine
    from runtime.risk_engine import RiskEngine
    from runtime.scheduler import Tick
    from runtime.supervisor import Supervisor

    class Hub:
        def snapshot(self, strategy_name, now):
            return _data_view()

    broker = Broker(BrokerSnapshot(account=AccountSnapshot(100_000.0, 100_000.0), positions={}))
    events = EventRecorder()
    status = StatusRecorder()
    supervisor = Supervisor(
        strategies={"demo": Strategy(PortfolioTarget.weights({"AAA": 1.0}, "entry"))},
        specs={"demo": _spec()},
        broker=broker,
        data_hub=Hub(),
        context=ContextBuilder(),
        risk_engine=RiskEngine(),
        portfolio_engine=PortfolioEngine(),
        execution_engine=ExecutionEngine(broker),
        events=events,
        status=status,
    )
    tick = Tick("demo", datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc), date(2026, 6, 12), "rebalance")

    asyncio.run(supervisor.on_tick(tick))

    assert [event for event, _ in events.events] == [
        "tick_started",
        "decision",
        "orders_submitted",
        "orders_filled",
        "status",
    ]
    assert broker.submitted[0].symbol == "AAA"
    assert status.rows[-1]["ready"] is True


def test_recovery_cancels_open_orders_and_flattens_unexpected_exposure():
    from domain.orders import OrderState
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, Position
    from runtime.recovery import BrokerRecovery

    class RecoveryBroker(Broker):
        async def list_open_orders(self, symbols):
            return [OrderState(id="old", client_order_id=None, symbol="AAA", side="buy", status="new")]

    broker = RecoveryBroker(
        BrokerSnapshot(
            account=AccountSnapshot(100_000.0, 100_000.0),
            positions={"AAA": Position("AAA", qty=10.0)},
        )
    )

    result = asyncio.run(BrokerRecovery(broker).recover((_spec(),)))

    assert result.clean is False
    assert result.ready is True
    assert broker.cancel_calls == [("AAA",)]
    assert broker.closed == ["AAA"]
    assert result.incidents == ("open_orders_cancelled:demo", "positions_flattened:demo")


def test_recovery_reports_clean_when_broker_state_needs_no_cleanup():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot
    from runtime.recovery import BrokerRecovery

    broker = Broker(BrokerSnapshot(account=AccountSnapshot(100_000.0, 100_000.0), positions={}))

    result = asyncio.run(BrokerRecovery(broker).recover((_spec(),)))

    assert result.clean is True
    assert result.ready is True
    assert result.incidents == ()


def test_runtime_app_is_lifecycle_only_and_delegates_ticks():
    from runtime.app import RuntimeApp
    from runtime.scheduler import Tick

    class Components:
        async def start(self):
            self.started = True
        async def stop(self, reason):
            self.stopped = reason

    class Scheduler:
        def due_ticks(self, now):
            return [Tick("demo", now, date(2026, 6, 12), "rebalance")]
        def sleep_seconds(self):
            return 60.0

    class Supervisor:
        def __init__(self):
            self.ticks = []
        async def on_tick(self, tick):
            self.ticks.append(tick)
        async def shutdown(self, reason):
            self.shutdown_reason = reason

    components = Components()
    supervisor = Supervisor()
    app = RuntimeApp(components=components, scheduler=Scheduler(), supervisor=supervisor)

    asyncio.run(app.start())
    asyncio.run(app.run_once(datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)))
    asyncio.run(app.stop("test"))

    assert components.started is True
    assert supervisor.ticks[0].strategy_name == "demo"
    assert supervisor.shutdown_reason == "test"
