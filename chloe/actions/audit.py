import json
from datetime import datetime
from chloe.state.db import get_connection
from chloe.actions.schema import Action, CostEstimate, DeliberationRecord, UserResponse


async def append(action: Action) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO actions (
            id, tool, verb, args, intent, cost_tokens, cost_usd,
            authorization, preview, proposed_at, state, result,
            error, deliberation, user_response, becomes_memory_id
        ) VALUES (
            :id, :tool, :verb, :args, :intent, :cost_tokens, :cost_usd,
            :authorization, :preview, :proposed_at, :state, :result,
            :error, :deliberation, :user_response, :becomes_memory_id
        )
        ON CONFLICT(id) DO UPDATE SET
            state             = excluded.state,
            result            = excluded.result,
            error             = excluded.error,
            deliberation      = excluded.deliberation,
            user_response     = excluded.user_response,
            becomes_memory_id = excluded.becomes_memory_id,
            cost_tokens       = excluded.cost_tokens,
            cost_usd          = excluded.cost_usd
        """,
        {
            "id":                action.id,
            "tool":              action.tool,
            "verb":              action.verb,
            "args":              json.dumps(action.args),
            "intent":            action.intent,
            "cost_tokens":       action.cost_estimate.tokens,
            "cost_usd":          action.cost_estimate.usd,
            "authorization":     action.authorization,
            "preview":           action.preview,
            "proposed_at":       action.proposed_at.isoformat(),
            "state":             action.state,
            "result":            json.dumps(action.result) if action.result else None,
            "error":             action.error,
            "deliberation":      json.dumps(action.deliberation.model_dump()) if action.deliberation else None,
            "user_response":     json.dumps(action.user_response.model_dump()) if action.user_response else None,
            "becomes_memory_id": action.becomes_memory_id,
        },
    )
    conn.commit()


async def recent(n: int = 200) -> list[Action]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM actions ORDER BY proposed_at DESC LIMIT ?", (n,)
    ).fetchall()
    return [_row_to_action(row) for row in rows]


def _row_to_action(row) -> Action:
    delib_raw = row["deliberation"]
    delib = DeliberationRecord(**json.loads(delib_raw)) if delib_raw and delib_raw != "null" else None

    ur_raw = row["user_response"]
    ur = UserResponse(**json.loads(ur_raw)) if ur_raw and ur_raw != "null" else None

    return Action(
        id=row["id"],
        tool=row["tool"],
        verb=row["verb"],
        args=json.loads(row["args"]) if row["args"] else {},
        intent=row["intent"],
        preview=row["preview"],
        authorization=row["authorization"],
        cost_estimate=CostEstimate(
            tokens=row["cost_tokens"],
            usd=row["cost_usd"],
        ),
        proposed_at=datetime.fromisoformat(row["proposed_at"]),
        state=row["state"],
        result=json.loads(row["result"]) if row["result"] else None,
        error=row["error"],
        deliberation=delib,
        user_response=ur,
        becomes_memory_id=row["becomes_memory_id"],
    )


_STATE_SYMBOL = {
    "executed":              "✓",
    "self_aborted":          "↩",
    "suppressed_by_leash":   "⊘",
    "denied":                "✗",
    "reverted":              "↩",
    "awaiting_confirmation": "?",
}


def feed_text(actions: list[Action], n: int = 10) -> str:
    if not actions:
        return "(no recent actions)"
    lines = []
    for a in actions[:n]:
        ts = a.proposed_at.strftime("%H:%M")
        intent = a.intent[:80] + "…" if len(a.intent) > 80 else a.intent
        symbol = _STATE_SYMBOL.get(a.state, "·")
        lines.append(f"[{ts}] {a.tool}.{a.verb} {symbol} — {intent}")
    return "\n".join(lines)
