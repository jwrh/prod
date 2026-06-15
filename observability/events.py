"""Append-only JSONL event sink."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


class JsonlEventSink:
    def __init__(self, log_dir: str | Path = "logs/events") -> None:
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def record(self, event_type: str, payload: Mapping[str, object]) -> None:
        now = datetime.now(timezone.utc)
        row = {"ts": now.isoformat(), "event": event_type, **dict(payload)}
        path = self._dir / f"{now.date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")


class NullEventSink:
    def record(self, event_type: str, payload: Mapping[str, object]) -> None:
        return None
