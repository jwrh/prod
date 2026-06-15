"""Isolated strategy evaluation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from multiprocessing import get_context
from typing import Any

from domain.portfolio import PortfolioTarget
from domain.strategy import Strategy, StrategyContext


@dataclass(frozen=True, slots=True)
class StrategySucceeded:
    target: PortfolioTarget


@dataclass(frozen=True, slots=True)
class StrategyFailed:
    error: str


@dataclass(frozen=True, slots=True)
class StrategyTimedOut:
    reason: str = "strategy_timeout"


StrategyRunResult = StrategySucceeded | StrategyFailed | StrategyTimedOut


class StrategyRunner:
    """Runs synchronous strategy code in a killable child process."""

    def __init__(self, *, timeout_seconds: float = 5.0, poll_seconds: float = 0.05) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        self._timeout_seconds = timeout_seconds
        self._poll_seconds = poll_seconds
        self._processes = get_context("fork")

    async def evaluate(self, strategy: Strategy, context: StrategyContext) -> StrategyRunResult:
        parent, child = self._processes.Pipe(duplex=False)
        process = self._processes.Process(target=_evaluate_strategy, args=(strategy, context, child))
        process.start()
        child.close()
        try:
            return await self._wait_for_result(process, parent)
        finally:
            parent.close()

    async def _wait_for_result(self, process, connection) -> StrategyRunResult:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        while loop.time() < deadline:
            if connection.poll():
                return await self._completed_result(process, connection.recv())
            if not process.is_alive():
                break
            await asyncio.sleep(min(self._poll_seconds, max(0.0, deadline - loop.time())))
        if process.is_alive():
            process.terminate()
            await asyncio.to_thread(process.join, 1.0)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 1.0)
            return StrategyTimedOut()
        if connection.poll():
            return await self._completed_result(process, connection.recv())
        return StrategyFailed(f"strategy process exited with code {process.exitcode}")

    async def _completed_result(self, process, message: tuple[Any, ...]) -> StrategyRunResult:
        await asyncio.to_thread(process.join, 1.0)
        return self._result_from_message(message)

    def _result_from_message(self, message: tuple[Any, ...]) -> StrategyRunResult:
        match message:
            case ("ok", action, weights, reason):
                return StrategySucceeded(PortfolioTarget(action, weights, reason))
            case ("error", error):
                return StrategyFailed(str(error))
            case _:
                return StrategyFailed("strategy process returned an invalid result")


def _evaluate_strategy(strategy: Strategy, context: StrategyContext, connection) -> None:
    try:
        target = strategy.evaluate(context)
        connection.send(("ok", target.action, dict(target.weights), target.reason))
    except BaseException as exc:
        connection.send(("error", f"{type(exc).__name__}: {exc}"))
    finally:
        connection.close()
