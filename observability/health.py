"""Status healthcheck helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

MAX_STALENESS_SECONDS = 300.0


def check_result(path: str | Path = "logs/status.json", *, required_strategies: Sequence[str] = ()) -> tuple[int, str]:
    path = Path(path)
    if not path.exists():
        return 2, f"status missing: {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 2, "status unreadable"
    try:
        ts = datetime.fromisoformat(payload["ts"])
    except Exception:
        return 2, "status timestamp invalid"
    if ts.tzinfo is None:
        return 2, "status timestamp missing timezone"
    age = (datetime.now(timezone.utc) - ts).total_seconds()
    if age > MAX_STALENESS_SECONDS:
        return 2, f"status stale: {age:.0f}s"
    if payload.get("ready") is not True:
        return 1, "not ready"
    if payload.get("status") != "running":
        return 1, f"status={payload.get('status')}"
    active = set(payload.get("active_strategies") or [])
    missing = [name for name in required_strategies if name not in active]
    if missing:
        return 1, f"required strategies not active: {','.join(missing)}"
    return 0, ""
