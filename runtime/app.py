"""Runtime application lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Protocol


class _Components(Protocol):
    async def start(self) -> None: ...
    async def reconnect(self) -> None: ...
    async def stop(self, reason: str) -> None: ...


class RuntimeApp:
    """Lifecycle wrapper that delegates all trading decisions."""

    def __init__(self, *, components, scheduler, supervisor, clock=None, sleep=asyncio.sleep) -> None:
        self._components = components
        self._scheduler = scheduler
        self._supervisor = supervisor
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep
        self._stopping = False

    async def start(self) -> None:
        await self._components.start()

    async def run_once(self, now=None) -> None:
        current = now or self._clock()
        ticks = self._scheduler.due_ticks(current)
        if not ticks:
            return
        results = await asyncio.gather(
            *(self._supervisor.on_tick(tick) for tick in ticks),
            return_exceptions=True,
        )
        if any(isinstance(result, ConnectionError) for result in results):
            await self._components.reconnect()
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, ConnectionError):
                raise result

    async def run_forever(self) -> None:
        while not self._stopping:
            await self.run_once()
            await self._sleep(self._scheduler.sleep_seconds())

    async def stop(self, reason: str = "shutdown") -> None:
        self._stopping = True
        await self._supervisor.shutdown(reason)
        await self._components.stop(reason)
