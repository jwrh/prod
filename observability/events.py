"""Append-only JSONL event sink."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


class JsonlEventSink:
    def __init__(
        self,
        log_dir: str | Path = "logs/events",
        *,
        run_id: str | None = None,
        mode: str | None = None,
    ) -> None:
        self._dir = Path(log_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._mode = mode
        self._seq = 0

    def record(self, event_type: str, payload: Mapping[str, object]) -> None:
        now = datetime.now(timezone.utc)
        self._seq += 1
        row = {
            **dict(payload),
            "ts": now.isoformat(),
            "event": event_type,
            **self._envelope(),
            "seq": self._seq,
        }
        path = self._dir / f"{now.date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")

    def _envelope(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {"run_id": self._run_id, "mode": self._mode}.items()
            if value is not None
        }


class NullEventSink:
    def record(self, event_type: str, payload: Mapping[str, object]) -> None:
        return None
