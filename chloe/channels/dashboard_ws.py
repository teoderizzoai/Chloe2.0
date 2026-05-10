"""Dashboard WebSocket — pushes live ticks, events, and affect snapshots.

The React dashboard currently polls `/v1/state/now` every 15s. This endpoint
gives it a streaming alternative: connect, receive an initial snapshot, then
deltas every 2 seconds.

Mounted at `/v1/dashboard/ws` by `chloe.app`.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from chloe.observability import live_buffer
from chloe.observability.logging import get_logger

log = get_logger("channels.dashboard_ws")

router = APIRouter()

PUSH_INTERVAL_SEC = 2.0


def _snapshot_for_client() -> dict:
    snap = live_buffer.snapshot()
    return {
        "ticks": snap["ticks"][:20],
        "events": snap["events"][:20],
        "affect": snap["affect"][:1],
    }


@router.websocket("/v1/dashboard/ws")
async def dashboard_ws(ws: WebSocket):
    await ws.accept()
    log.info("dashboard_ws_connected")

    last_seen = {"tick_ts": None, "event_ts": None, "affect_ts": None}

    try:
        await ws.send_text(json.dumps({"type": "snapshot", "data": _snapshot_for_client()}))

        while True:
            await asyncio.sleep(PUSH_INTERVAL_SEC)
            snap = live_buffer.snapshot()

            new_ticks = _drop_seen(snap["ticks"], last_seen, "tick_ts")
            new_events = _drop_seen(snap["events"], last_seen, "event_ts")
            new_affect = _drop_seen(snap["affect"], last_seen, "affect_ts")

            if not (new_ticks or new_events or new_affect):
                continue

            await ws.send_text(json.dumps({
                "type": "delta",
                "ticks": new_ticks,
                "events": new_events,
                "affect": new_affect,
            }))
    except WebSocketDisconnect:
        log.info("dashboard_ws_disconnected")
    except Exception as exc:
        log.warning("dashboard_ws_error", error=str(exc))
        try:
            await ws.close()
        except Exception:
            pass


def _drop_seen(items: list[dict], state: dict, key: str) -> list[dict]:
    """Keep items strictly newer than the last-seen timestamp; advance the cursor."""
    last_ts = state.get(key)
    new = []
    for item in items:
        ts = item.get("timestamp")
        if last_ts is None or (ts and ts > last_ts):
            new.append(item)
    if new:
        state[key] = new[0].get("timestamp")
    return new
