"""Startup and reconnect broker-truth recovery."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ports import BrokerPort
from domain.strategy import StrategySpec


@dataclass(frozen=True)
class RecoveryResult:
    clean: bool
    ready: bool
    incidents: tuple[str, ...]


class BrokerRecovery:
    """Cancels stale orders and flattens unexpected managed exposure."""

    def __init__(self, broker: BrokerPort) -> None:
        self._broker = broker

    async def recover(self, specs: tuple[StrategySpec, ...]) -> RecoveryResult:
        snapshot = await self._broker.snapshot()
        incidents: list[str] = []
        for spec in specs:
            open_orders = list(await self._broker.list_open_orders(spec.universe))
            if open_orders:
                await self._broker.cancel_open_orders(spec.universe)
                incidents.append(f"open_orders_cancelled:{spec.name}")
            positioned = [
                symbol
                for symbol, position in snapshot.positions.items()
                if symbol in spec.universe and position.qty != 0.0
            ]
            if positioned and not spec.allow_adoption:
                await self._broker.close_positions(tuple(positioned))
                incidents.append(f"positions_flattened:{spec.name}")
        return RecoveryResult(clean=not incidents, ready=True, incidents=tuple(incidents))
