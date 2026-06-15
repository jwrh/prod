from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np


def _ctx(weights=None, trigger="rebalance"):
    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot
    from domain.strategy import StrategyContext

    return StrategyContext(
        strategy="demo",
        now=datetime(2026, 6, 12, 14, 30, tzinfo=timezone.utc),
        session=date(2026, 6, 12),
        trigger=trigger,
        universe=("AAA", "BBB"),
        prices={"AAA": 25.0, "BBB": 30.0},
        bid_ask={"AAA": BidAsk(24.99, 25.01), "BBB": BidAsk(29.99, 30.01)},
        windows={"fast": np.array([[25.0, 25.0], [25.0, 26.0], [25.0, 30.0]])},
        account=AccountSnapshot(100_000.0, 100_000.0),
        positions={},
        current_weights=weights or {},
    )


def _spec(params=None):
    from domain.portfolio import RiskSpec, VenueRule
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec

    return StrategySpec(
        name="demo",
        class_path="strategies.dummy.DummyStrategy",
        universe=("AAA", "BBB"),
        schedule=ScheduleSpec("1m"),
        data=StrategyDataSpec((DataWindowSpec("fast", "1m", 3),)),
        capital=CapitalSpec(10_000.0),
        risk=RiskSpec(venue_rules={"AAA": VenueRule(), "BBB": VenueRule()}),
        params=params or {"weights": {"AAA": 0.6, "BBB": 0.4}},
    )


def test_dummy_strategy_uses_strategy_context_contract():
    from strategies.dummy import DummyStrategy

    target = DummyStrategy(_spec()).evaluate(_ctx())

    assert target.action == "target"
    assert set(target.weights) == {"AAA", "BBB"}
    assert target.reason == "dummy_target"


def test_dummy_strategy_flattens_on_pre_close_trigger():
    from strategies.dummy import DummyStrategy

    target = DummyStrategy(_spec()).evaluate(_ctx({"AAA": 0.5}, trigger="pre_close"))

    assert target.action == "flat"
    assert target.reason == "risk_exit"


def test_cli_status_uses_healthcheck(tmp_path, capsys):
    from cli import main
    from observability.status import StatusWriter

    status = tmp_path / "status.json"
    StatusWriter(status).write({"ready": True, "status": "running", "active_strategies": ["demo"]})

    assert main(["status", "--status", str(status), "--required-strategy", "demo"]) == 0
    assert "status: ok" in capsys.readouterr().out
