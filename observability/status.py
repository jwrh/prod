"""Compact runtime status writer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


class StatusWriter:
    def __init__(self, path: str | Path = "logs/status.json") -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, payload: Mapping[str, object]) -> None:
        row = {"ts": datetime.now(timezone.utc).isoformat(), **dict(payload)}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(row, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        tmp.replace(self._path)


class NullStatusWriter:
    def write(self, payload: Mapping[str, object]) -> None:
        return None
