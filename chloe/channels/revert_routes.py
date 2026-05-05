import json
from datetime import datetime

from fastapi import APIRouter, HTTPException

from chloe.actions.schema import ulid
from chloe.observability.logging import get_logger
from chloe.state.db import get_connection
from chloe.tools.registry import get_registry

log = get_logger("revert")
router = APIRouter(prefix="/v1/actions", tags=["actions"])


@router.post("/{action_id}/revert")
async def revert_action(action_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Action not found")

    if row["state"] != "executed":
        raise HTTPException(status_code=409, detail=f"Cannot revert action in state '{row['state']}'")

    registry = get_registry()
    tool = registry.get_tool(row["tool"])
    if not tool:
        raise HTTPException(status_code=422, detail=f"Tool '{row['tool']}' not found")

    verb_def = tool.verbs.get(row["verb"])
    if not verb_def or not verb_def.reverse_verb:
        raise HTTPException(status_code=422, detail=f"No reverse verb for {row['tool']}.{row['verb']}")

    original_result = json.loads(row["result"] or "{}")
    original_args = json.loads(row["args"] or "{}")
    reverse_args = _build_reverse_args(row["tool"], row["verb"], original_result, original_args)

    reverse_result = await registry.execute(row["tool"], verb_def.reverse_verb, reverse_args)

    if not reverse_result.success:
        raise HTTPException(status_code=502, detail=f"Revert failed: {reverse_result.error}")

    user_response = {"kind": "revert", "reverted_at": datetime.utcnow().isoformat()}
    conn.execute(
        "UPDATE actions SET state='reverted', user_response=? WHERE id=?",
        (json.dumps(user_response), action_id),
    )

    memory_id_val = ulid()
    conn.execute(
        """INSERT INTO memories (kind, text, source, source_ref, artifact_refs, weight, tags, created_at)
           VALUES ('episodic', ?, 'revert', ?, '[]', 0.9, '["held_back","reverted"]', ?)""",
        (f"Teo reverted: {row['intent']}", action_id, datetime.utcnow().isoformat()),
    )
    conn.commit()

    log.info("action_reverted", action_id=action_id, reverse_verb=verb_def.reverse_verb)
    return {"status": "reverted", "action_id": action_id, "reverse_verb": verb_def.reverse_verb}


def _build_reverse_args(tool: str, verb: str, result: dict, original_args: dict) -> dict:
    if tool == "calendar" and verb == "add_event":
        return {"eventId": result.get("eventId", "")}
    if tool == "notes" and verb == "append":
        return {"path": original_args.get("path", "")}
    if tool == "spotify" and verb == "queue_track":
        return {}
    return {}
