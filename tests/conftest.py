"""Shared pytest path setup."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class AsyncGate:
    def __init__(self, expected: int = 2):
        self.expected = expected
        self.started = []
        self._ready = asyncio.Event()
        self._release = asyncio.Event()

    async def enter(self, marker):
        self.started.append(marker)
        if len(self.started) == self.expected:
            self._ready.set()
        await self._release.wait()

    async def wait_ready(self, timeout: float = 0.2):
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)

    def release_all(self):
        self._release.set()

    async def wait_and_release(self, timeout: float = 0.2):
        await self.wait_ready(timeout=timeout)
        self.release_all()


@pytest.fixture
def async_gate():
    return AsyncGate
