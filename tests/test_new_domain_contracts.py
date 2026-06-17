from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pytest


def test_strategy_contract_accepts_hold_target_and_flat():
    from domain.market import BidAsk
    from domain.portfolio import AccountSnapshot, PortfolioTarget, Position
    from domain.strategy import StrategyContext

    ctx = StrategyContext(
        strategy="demo",
        now=datetime(2026, 6, 14, 16, 0, tzinfo=timezone.utc),
        session=date(2026, 6, 14),
        trigger="rebalance",
        universe=("AAA", "BBB"),
        prices={"AAA": 25.0, "BBB": 30.0},
        bid_ask={"AAA": BidAsk(24.99, 25.01), "BBB": BidAsk(29.99, 30.01)},
        windows={"fast": np.array([[24.0, 29.0], [25.0, 30.0]])},
        account=AccountSnapshot(equity=100_000.0, cash=50_000.0),
        positions={"AAA": Position(symbol="AAA", qty=10.0)},
        current_weights={"AAA": 0.0025},
    )

    assert ctx.universe == ("AAA", "BBB")
    assert PortfolioTarget.hold("idle").action == "hold"
    assert PortfolioTarget.flat("eod").action == "flat"
    assert PortfolioTarget.weights({"AAA": 1.0, "BBB": -1.0}, "entry").weights["BBB"] == -1.0


def test_contracts_reject_invalid_market_and_target_values():
    from domain.market import BidAsk, Quote
    from domain.orders import OrderIntent
    from domain.portfolio import AccountSnapshot, BrokerSnapshot, PortfolioTarget, Position
    from domain.strategy import CapitalSpec, DataWindowSpec, ScheduleSpec, StrategyDataSpec, StrategySpec

    with pytest.raises(ValueError, match="bid < ask"):
        BidAsk(10.0, 10.0)
    with pytest.raises(ValueError, match="equity"):
        AccountSnapshot(equity=0.0, cash=1.0)
    with pytest.raises(ValueError, match="reason"):
        PortfolioTarget.hold("")
    with pytest.raises(ValueError, match="duplicate target symbol: AAA"):
        PortfolioTarget.weights({"AAA": 0.6, "aaa": 0.4}, "bad")
    with pytest.raises(ValueError, match="exactly one"):
        OrderIntent(symbol="AAA", side="buy", qty=1.0, notional=10.0)
    with pytest.raises(ValueError, match="quote timestamp must include timezone"):
        Quote("AAA", 25.0, now=datetime(2026, 6, 14, 16, 0))
    with pytest.raises(ValueError, match="position key AAA does not match position symbol BBB"):
        BrokerSnapshot(
            AccountSnapshot(equity=100_000.0, cash=100_000.0),
            positions={"AAA": Position("BBB", qty=10.0)},
        )
    with pytest.raises(ValueError, match="duplicate data window name: fast"):
        StrategyDataSpec(
            (
                DataWindowSpec("fast", "1m", 2),
                DataWindowSpec("fast", "5m", 2),
            )
        )
    with pytest.raises(ValueError, match="duplicate universe symbol: AAA"):
        StrategySpec(
            name="demo",
            class_path="strategies.dummy.DummyStrategy",
            universe=("AAA", "aaa"),
            schedule=ScheduleSpec("1m"),
            data=StrategyDataSpec((DataWindowSpec("fast", "1m", 2),)),
            capital=CapitalSpec(10_000.0),
        )


def test_runtime_import_boundaries_are_clean():
    import ast
    from pathlib import Path

    root = Path("runtime")
    for path in root.glob("*.py"):
        assert len(path.read_text().splitlines()) <= 300, path
        tree = ast.parse(path.read_text())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        if path.name not in {"factories.py"}:
            assert not any(name.startswith("adapters") for name in imports), path
        if path.name == "app.py":
            banned = {
                "runtime.portfolio_engine",
                "runtime.execution_engine",
                "runtime.data_hub",
            }
            assert not banned.intersection(imports)
