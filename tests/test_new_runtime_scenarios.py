from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone


def _spec(name="demo", universe=("AAA",), *, risk=None):
    from domain.portfolio import RiskSpec, VenueRule
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec

    return StrategySpec(
        name=name,
        class_path="tests.fake.Demo",
        universe=universe,
        schedule=ScheduleSpec(rebalance="1m"),
        data=StrategyDataSpec(windows=(DataWindowSpec(name="fast", interval="1m", lookback=2),)),
        capital=CapitalSpec(amount=10_000.0),
        risk=risk or RiskSpec(venue_rules={symbol: VenueRule() for symbol in universe}),
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


def test_supervisor_order_ids_are_stable_within_schedule_bucket():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget
    from runtime.context import ContextBuilder
    from runtime.execution_engine import ExecutionEngine
    from runtime.portfolio_engine import PortfolioEngine
    from runtime.risk_engine import RiskEngine
    from runtime.scheduler import Tick
    from runtime.supervisor import Supervisor

    def submitted_id_at(now):
        class Hub:
            def snapshot(self, strategy_name, now):
                return _data_view()

        broker = Broker(BrokerSnapshot(account=AccountSnapshot(100_000.0, 100_000.0), positions={}))
        supervisor = Supervisor(
            strategies={"demo": Strategy(PortfolioTarget.weights({"AAA": 1.0}, "entry"))},
            specs={"demo": _spec()},
            broker=broker,
            data_hub=Hub(),
            context=ContextBuilder(),
            risk_engine=RiskEngine(),
            portfolio_engine=PortfolioEngine(),
            execution_engine=ExecutionEngine(broker),
            events=EventRecorder(),
            status=StatusRecorder(),
        )
        tick = Tick("demo", now, date(2026, 6, 12), "rebalance")

        asyncio.run(supervisor.on_tick(tick))

        return broker.submitted[0].client_order_id

    first = submitted_id_at(datetime(2026, 6, 12, 14, 30, 5, tzinfo=timezone.utc))
    second = submitted_id_at(datetime(2026, 6, 12, 14, 30, 20, tzinfo=timezone.utc))

    assert first == second


def test_supervisor_block_status_preserves_healthcheck_strategy_identity():
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget
    from runtime.context import ContextBuilder
    from runtime.execution_engine import ExecutionEngine
    from runtime.portfolio_engine import PortfolioEngine
    from runtime.risk_engine import RiskEngine
    from runtime.scheduler import Tick
    from runtime.supervisor import Supervisor

    class Hub:
        def snapshot(self, strategy_name, now):
            return _data_view(ready=False, fresh=False)

    broker = Broker(BrokerSnapshot(account=AccountSnapshot(100_000.0, 100_000.0), positions={}))
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
        events=EventRecorder(),
        status=status,
    )
    tick = Tick("demo", datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc), date(2026, 6, 12), "rebalance")

    asyncio.run(supervisor.on_tick(tick))

    assert status.rows[-1]["ready"] is True
    assert status.rows[-1]["last_tick"] == "demo"
    assert status.rows[-1]["active_strategies"] == ["demo"]
    assert status.rows[-1]["last_block"] == "missing_prices"


def test_supervisor_shutdown_flattens_strategy_universes_concurrently(async_gate):
    from runtime.supervisor import Supervisor

    class Execution:
        def __init__(self, gate):
            self.gate = gate

        async def flatten(self, spec):
            await self.gate.enter(spec.name)
            return []

    class Status:
        def __init__(self):
            self.rows = []

        def write(self, payload):
            self.rows.append(payload)

    async def run():
        gate = async_gate()
        execution = Execution(gate)
        status = Status()
        supervisor = Supervisor(
            strategies={},
            specs={"alpha": _spec("alpha", ("AAA",)), "beta": _spec("beta", ("BBB",))},
            broker=None,
            data_hub=None,
            context=None,
            risk_engine=None,
            portfolio_engine=None,
            execution_engine=execution,
            events=None,
            status=status,
        )
        task = asyncio.create_task(supervisor.shutdown("test"))
        await gate.wait_and_release()
        await task
        return gate, status

    gate, status = asyncio.run(run())

    assert gate.started == ["alpha", "beta"]
    assert status.rows[-1] == {"ready": True, "status": "stopped", "reason": "test"}


def test_strategy_runner_terminates_timed_out_evaluation_process():
    import time

    from domain.portfolio import AccountSnapshot, BrokerSnapshot
    from runtime.context import ContextBuilder, ContextReady
    from runtime.strategy_runner import StrategyRunner, StrategyTimedOut

    class SlowStrategy:
        def evaluate(self, ctx):
            time.sleep(5.0)
            raise AssertionError("terminated evaluation should not return")

    built = ContextBuilder().build(
        _spec(),
        _data_view(),
        BrokerSnapshot(account=AccountSnapshot(100_000.0, 100_000.0), positions={}),
        date(2026, 6, 12),
        "rebalance",
    )
    assert isinstance(built, ContextReady)

    result = asyncio.run(StrategyRunner(timeout_seconds=0.05, poll_seconds=0.01).evaluate(SlowStrategy(), built.context))

    assert isinstance(result, StrategyTimedOut)
    assert result.reason == "strategy_timeout"


def test_execution_engine_submits_same_side_orders_concurrently_before_buys(async_gate):
    from domain.orders import OrderIntent, OrderState
    from runtime.execution_engine import ExecutionEngine

    spec = _spec(universe=("AAA", "BBB", "CCC"))

    class Broker:
        def __init__(self, sell_gate):
            self.sell_gate = sell_gate
            self.started = []
            self.sell_completed = []

        async def list_open_orders(self, symbols):
            return []

        async def cancel_open_orders(self, symbols): ...
        async def close_positions(self, symbols): ...

        async def submit(self, order):
            self.started.append((order.symbol, order.side))
            if order.side == "sell":
                await self.sell_gate.enter(order.symbol)
                self.sell_completed.append(order.symbol)
            else:
                assert len(self.sell_completed) == 2
            return OrderState(
                id=f"order-{len(self.started)}",
                client_order_id=order.client_order_id,
                symbol=order.symbol,
                side=order.side,
                status="filled",
                filled_qty=order.qty or 1.0,
                filled_avg_price=25.0,
            )

        async def get_order(self, order_id):
            return None

    async def run():
        sell_gate = async_gate()
        broker = Broker(sell_gate)
        engine = ExecutionEngine(broker)
        task = asyncio.create_task(
            engine.execute(
                spec,
                [
                    OrderIntent("AAA", "sell", qty=1.0),
                    OrderIntent("BBB", "sell", qty=1.0),
                    OrderIntent("CCC", "buy", qty=1.0),
                ],
            )
        )
        await sell_gate.wait_ready()
        assert broker.started == [("AAA", "sell"), ("BBB", "sell")]
        sell_gate.release_all()
        fills = await task
        return broker, fills

    broker, fills = asyncio.run(run())

    assert broker.started == [("AAA", "sell"), ("BBB", "sell"), ("CCC", "buy")]
    assert [fill.symbol for fill in fills] == ["AAA", "BBB", "CCC"]


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


def test_recovery_checks_independent_strategy_universes_concurrently(async_gate):
    from domain.orders import OrderState
    from domain.portfolio import AccountSnapshot, BrokerSnapshot
    from runtime.recovery import BrokerRecovery

    class Broker:
        def __init__(self, gate):
            self.gate = gate

        async def snapshot(self):
            return BrokerSnapshot(account=AccountSnapshot(100_000.0, 100_000.0), positions={})

        async def list_open_orders(self, symbols):
            await self.gate.enter(tuple(symbols))
            return [OrderState("old", None, symbols[0], "buy", "new")]

        async def cancel_open_orders(self, symbols): ...
        async def close_positions(self, symbols): ...

    async def run():
        gate = async_gate()
        broker = Broker(gate)
        recovery = BrokerRecovery(broker)
        task = asyncio.create_task(recovery.recover((_spec("alpha", ("AAA",)), _spec("beta", ("BBB",)))))
        await gate.wait_and_release()
        return await task, gate

    result, gate = asyncio.run(run())

    assert gate.started == [("AAA",), ("BBB",)]
    assert result.incidents == ("open_orders_cancelled:alpha", "open_orders_cancelled:beta")


def test_runtime_components_reconnect_rewarms_data_and_recovers_broker_state(async_gate):
    from runtime.composition import RuntimeComponents

    class DataHub:
        def __init__(self, gate):
            self.calls = []
            self.gate = gate

        def mark_disconnected(self):
            self.calls.append(("mark_disconnected", None))

        async def disconnect(self):
            self.calls.append(("disconnect", None))

        async def connect(self):
            self.calls.append(("connect", None))

        async def warmup(self, specs):
            self.calls.append(("warmup", specs))
            await self.gate.enter("warmup")

    class Recovery:
        def __init__(self, gate):
            self.calls = []
            self.gate = gate

        async def recover(self, specs):
            self.calls.append(specs)
            await self.gate.enter("recover")

    specs = (_spec(),)
    gate = async_gate()
    data_hub = DataHub(gate)
    recovery = Recovery(gate)
    components = RuntimeComponents(data_hub=data_hub, recovery=recovery, specs=specs)

    async def run():
        task = asyncio.create_task(components.reconnect())
        await gate.wait_and_release()
        await task

    asyncio.run(run())

    assert data_hub.calls == [
        ("mark_disconnected", None),
        ("disconnect", None),
        ("connect", None),
        ("warmup", specs),
    ]
    assert recovery.calls == [specs]
    assert set(gate.started) == {"warmup", "recover"}


def test_runtime_app_reconnects_components_after_connection_failure():
    from runtime.app import RuntimeApp
    from runtime.scheduler import Tick

    class Components:
        def __init__(self):
            self.reconnects = 0

        async def start(self): ...
        async def stop(self, reason): ...
        async def reconnect(self):
            self.reconnects += 1

    class Scheduler:
        def due_ticks(self, now):
            return [Tick("demo", now, date(2026, 6, 12), "rebalance")]
        def sleep_seconds(self):
            return 60.0

    class Supervisor:
        async def on_tick(self, tick):
            raise ConnectionError("feed disconnected")
        async def shutdown(self, reason): ...

    components = Components()
    app = RuntimeApp(components=components, scheduler=Scheduler(), supervisor=Supervisor())

    asyncio.run(app.run_once(datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)))

    assert components.reconnects == 1


def test_runtime_app_runs_due_strategy_ticks_concurrently(async_gate):
    from runtime.app import RuntimeApp
    from runtime.scheduler import Tick

    class Components:
        async def start(self): ...
        async def stop(self, reason): ...
        async def reconnect(self): ...

    class Scheduler:
        def due_ticks(self, now):
            return [
                Tick("alpha", now, date(2026, 6, 12), "rebalance"),
                Tick("beta", now, date(2026, 6, 12), "rebalance"),
            ]

        def sleep_seconds(self):
            return 60.0

    class Supervisor:
        def __init__(self, gate):
            self.gate = gate
            self.completed = []

        async def on_tick(self, tick):
            await self.gate.enter(tick.strategy_name)
            self.completed.append(tick.strategy_name)

        async def shutdown(self, reason): ...

    async def run():
        gate = async_gate()
        supervisor = Supervisor(gate)
        app = RuntimeApp(components=Components(), scheduler=Scheduler(), supervisor=supervisor)
        task = asyncio.create_task(app.run_once(datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc)))
        await gate.wait_and_release()
        await task
        return supervisor, gate

    supervisor, gate = asyncio.run(run())

    assert gate.started == ["alpha", "beta"]
    assert set(supervisor.completed) == {"alpha", "beta"}


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
