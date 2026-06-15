"""Strategy rebalance scheduling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from domain.market import INTERVAL_SECONDS
from domain.strategy import StrategySpec


@dataclass(frozen=True)
class Tick:
    strategy_name: str
    now: datetime
    session: date
    trigger: str


class RuntimeScheduler:
    """Emits ticks only for strategies due at the current timestamp."""

    def __init__(self, specs: tuple[StrategySpec, ...], *, poll_seconds: float = 1.0) -> None:
        self._specs = specs
        self._poll_seconds = poll_seconds
        self._last_bucket: dict[str, int] = {}

    def due_ticks(self, now: datetime) -> list[Tick]:
        due: list[Tick] = []
        for spec in self._specs:
            seconds = INTERVAL_SECONDS[spec.schedule.rebalance]
            bucket = self._bucket(now, seconds)
            if self._last_bucket.get(spec.name) == bucket:
                continue
            self._last_bucket[spec.name] = bucket
            due.append(Tick(spec.name, now, now.date(), "rebalance"))
        return due

    def sleep_seconds(self) -> float:
        return self._poll_seconds

    def _bucket(self, now: datetime, seconds: int) -> int:
        if seconds >= 86_400:
            return now.date().toordinal()
        return int(now.timestamp()) // seconds
