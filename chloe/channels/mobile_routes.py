from __future__ import annotations

import json

from fastapi import APIRouter, Query, WebSocket
from pydantic import BaseModel

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("channels.mobile_routes")

router = APIRouter(tags=["mobile"])


# ── F-M05 · Activity tab (audit feed) ────────────────────────────────────────

@router.get("/v1/audit")
async def mobile_audit(
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, tool, verb, intent, preview, state, authorization,
                  proposed_at, cost_usd, error
           FROM actions
           ORDER BY proposed_at DESC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    items = [
        {
            "id": r["id"],
            "tool": r["tool"],
            "verb": r["verb"],
            "intent": r["intent"],
            "preview": r["preview"],
            "state": r["state"],
            "authorization": r["authorization"],
            "proposed_at": r["proposed_at"],
            "cost_usd": r["cost_usd"],
            "error": r["error"],
        }
        for r in rows
    ]
    return {"count": len(items), "offset": offset, "actions": items}


# ── F-M06 · "Now" tab (Chloe's state) ───────────────────────────────────────

@router.get("/v1/state/now")
async def state_now() -> dict:
    from chloe.state.kv import get as kv_get
    from chloe.affect.dims import load as load_affect, tone_block

    affect = load_affect()

    conn = get_connection()
    goals = conn.execute(
        "SELECT id, name, progress FROM inner_goals WHERE status='active' LIMIT 10"
    ).fetchall()
    traits = conn.execute(
        "SELECT name, weight FROM identity_traits WHERE status IN ('active','core') ORDER BY weight DESC LIMIT 3"
    ).fetchall()

    return {
        "current_activity": kv_get("current_activity", default=""),
        "affect_label": kv_get("affect_label_cache", {}).get("label", ""),
        "tone": tone_block(affect),
        "goals": [
            {"id": g["id"], "name": g["name"], "progress": g["progress"]}
            for g in goals
        ],
        "top_interests": [
            {"label": t["name"], "intensity": t["weight"]}
            for t in traits
        ],
    }


# ── F-M07 · Leash settings screen ────────────────────────────────────────────

class PreferencePatch(BaseModel):
    key: str
    value: object


@router.patch("/v1/preferences")
async def update_preference(patch: PreferencePatch) -> dict:
    conn = get_connection()
    conn.execute(
        "INSERT INTO preferences (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (patch.key, json.dumps(patch.value)),
    )
    conn.commit()
    log.info("preference_updated", key=patch.key)
    return {"status": "ok", "key": patch.key}


# ── F-M08 · OAuth revoke ─────────────────────────────────────────────────────

@router.delete("/v1/oauth/{service}")
async def revoke_oauth(service: str) -> dict:
    from chloe.state.oauth_tokens import store
    store(service, {})
    log.info("oauth_revoked", service=service)
    return {"status": "revoked", "service": service}


# ── Mobile WebSocket (F-M02) ─────────────────────────────────────────────────

@router.websocket("/v1/mobile/ws")
async def mobile_ws(websocket: WebSocket, person_id: str = "1") -> None:
    from chloe.channels.mobile_ws import handle_mobile_ws
    await handle_mobile_ws(websocket, person_id=person_id)


# ── Voice WebSocket (F-V03 / F-M10) ─────────────────────────────────────────

@router.websocket("/v1/voice")
async def voice_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    from chloe.voice.realtime import handle_voice_session
    await handle_voice_session(websocket)
