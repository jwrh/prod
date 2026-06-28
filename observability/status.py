"""Compact runtime status writer."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


class StatusWriter:
    def __init__(
        self,
        path: str | Path = "logs/status.json",
        *,
        run_id: str | None = None,
        mode: str | None = None,
    ) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._mode = mode

    def write(self, payload: Mapping[str, object]) -> None:
        row = {
            **dict(payload),
            "ts": datetime.now(timezone.utc).isoformat(),
            **self._envelope(),
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(row, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        tmp.replace(self._path)

    def _envelope(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {"run_id": self._run_id, "mode": self._mode}.items()
            if value is not None
        }


class NullStatusWriter:
    def write(self, payload: Mapping[str, object]) -> None:
        return None
