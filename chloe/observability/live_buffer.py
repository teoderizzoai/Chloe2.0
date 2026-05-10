"""In-memory ring buffers for live UI: heartbeat ticks, incoming events, affect snapshots."""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime
from typing import Any

_MAX = 200
_lock = threading.Lock()
_ticks: deque[dict[str, Any]] = deque(maxlen=_MAX)
_events: deque[dict[str, Any]] = deque(maxlen=_MAX)
_affect: deque[dict[str, Any]] = deque(maxlen=_MAX)


def _now() -> str:
    return datetime.utcnow().isoformat()


def record_tick(entry: dict[str, Any]) -> None:
    entry.setdefault("timestamp", _now())
    with _lock:
        _ticks.append(entry)


def record_event(source: str, summary: str, **extra: Any) -> None:
    with _lock:
        _events.append({"timestamp": _now(), "source": source, "summary": summary, **extra})


def record_affect(snapshot: dict[str, Any]) -> None:
    with _lock:
        last = _affect[-1] if _affect else None
        keys = ("valence", "arousal", "dominance", "label", "current_activity")
        if last and all(last.get(k) == snapshot.get(k) for k in keys):
            return
        _affect.append({"timestamp": _now(), **snapshot})


def snapshot() -> dict[str, list[dict[str, Any]]]:
    with _lock:
        return {
            "ticks": list(_ticks)[::-1],
            "events": list(_events)[::-1],
            "affect": list(_affect)[::-1],
        }
