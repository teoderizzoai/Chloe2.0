import json

from fastapi import APIRouter
from pydantic import BaseModel

from chloe.state.db import get_connection

router = APIRouter(prefix="/admin/ha", tags=["ha-prefs"])


class EntityListUpdate(BaseModel):
    entities: list[str]


@router.get("/allowlist")
async def get_allowlist():
    conn = get_connection()
    row = conn.execute("SELECT value FROM preferences WHERE key='ha_allowlist'").fetchone()
    return {"entities": json.loads(row["value"]) if row else []}


@router.put("/allowlist")
async def set_allowlist(update: EntityListUpdate):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_allowlist", json.dumps(update.entities)),
    )
    conn.commit()
    return {"status": "updated", "entities": update.entities}


@router.get("/blocklist")
async def get_blocklist():
    conn = get_connection()
    row = conn.execute("SELECT value FROM preferences WHERE key='ha_blocklist'").fetchone()
    return {"entities": json.loads(row["value"]) if row else []}


@router.put("/blocklist")
async def set_blocklist(update: EntityListUpdate):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO preferences (key, value) VALUES (?, ?)",
        ("ha_blocklist", json.dumps(update.entities)),
    )
    conn.commit()
    return {"status": "updated", "entities": update.entities}
