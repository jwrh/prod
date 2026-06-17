"""Approved order execution against a broker port."""

from __future__ import annotations

import asyncio

from domain.orders import OrderIntent, OrderState
from domain.ports import BrokerPort
from domain.strategy import StrategySpec
from runtime.errors import BrokerAmbiguous, ExecutionFailed


TERMINAL_BAD = {"canceled", "cancelled", "rejected", "expired"}


class ExecutionEngine:
    """Submits approved diffs and proves terminal broker state."""

    def __init__(self, broker: BrokerPort, *, poll_seconds: float = 0.1, timeout_seconds: float = 10.0) -> None:
        self._broker = broker
        self._poll_seconds = poll_seconds
        self._timeout_seconds = timeout_seconds

    async def execute(self, spec: StrategySpec, intents: list[OrderIntent]) -> list[OrderState]:
        if not intents:
            return []
        open_orders = await self._broker.list_open_orders(spec.universe)
        if open_orders:
            await self._broker.cancel_open_orders(spec.universe)
        filled: list[OrderState] = []
        try:
            for batch in self._same_side_batches(intents):
                filled.extend(await self._execute_batch(batch))
        except Exception as exc:
            await self._broker.cancel_open_orders(spec.universe)
            await self._broker.close_positions(spec.universe)
            if isinstance(exc, BrokerAmbiguous):
                raise
            raise ExecutionFailed(str(exc)) from exc
        return filled

    async def flatten(self, spec: StrategySpec) -> list[OrderState]:
        await self._broker.cancel_open_orders(spec.universe)
        return list(await self._broker.close_positions(spec.universe))

    async def _execute_batch(self, intents: list[OrderIntent]) -> list[OrderState]:
        results = await asyncio.gather(
            *(self._submit_and_wait(intent) for intent in intents),
            return_exceptions=True,
        )
        filled = []
        for result in results:
            if isinstance(result, BaseException):
                raise result
            filled.append(result)
        return filled

    async def _submit_and_wait(self, intent: OrderIntent) -> OrderState:
        state = await self._broker.submit(intent)
        return await self._wait_terminal(state)

    def _same_side_batches(self, intents: list[OrderIntent]) -> tuple[list[OrderIntent], ...]:
        batches: list[list[OrderIntent]] = []
        for intent in intents:
            if not batches or batches[-1][-1].side != intent.side:
                batches.append([])
            batches[-1].append(intent)
        return tuple(batches)

    async def _wait_terminal(self, state: OrderState) -> OrderState:
        if state.status == "filled":
            return state
        if state.status in TERMINAL_BAD:
            raise ExecutionFailed(f"{state.id}: {state.status}")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        current = state
        while loop.time() < deadline:
            await asyncio.sleep(self._poll_seconds)
            refreshed = await self._broker.get_order(current.id)
            current = refreshed or current
            if current.status == "filled":
                return current
            if current.status in TERMINAL_BAD:
                raise ExecutionFailed(f"{current.id}: {current.status}")
        raise BrokerAmbiguous(f"{state.id}: timed out waiting for fill")
