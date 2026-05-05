import json
from datetime import datetime, timezone
from chloe.actions.schema import Action, ActionResult
from chloe.actions import audit, budget, leash as leash_mod
from chloe.tools.registry import get_registry
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger
from chloe.observability.metrics import record_action, record_held_back

log = get_logger("gate")


def _load_prefs() -> dict:
    conn = get_connection()
    rows = conn.execute("SELECT key, value FROM preferences").fetchall()
    result = {}
    for r in rows:
        v = r["value"]
        result[r["key"]] = json.loads(v) if isinstance(v, str) else v
    return result


async def submit(action: Action) -> ActionResult:
    now = datetime.now(timezone.utc)
    prefs = _load_prefs()

    # 1. Leash check
    violated, reason = leash_mod.violates(action, prefs, now)
    if violated:
        action.state = "suppressed_by_leash"
        await audit.append(action)
        await _store_held_back_memory(action, reason)
        record_action(action.tool, action.verb, "suppressed_by_leash")
        record_held_back("leash")
        log.info("gate_suppressed", action_id=action.id, reason=reason)
        return ActionResult(suppressed=True, reason=reason, action_id=action.id)

    # 2. Budget check
    if budget.exceeded_for(action):
        action.state = "self_aborted"
        await audit.append(action)
        await _store_held_back_memory(action, "budget_exceeded")
        record_action(action.tool, action.verb, "self_aborted")
        record_held_back("budget")
        log.info("gate_budget_exceeded", action_id=action.id)
        return ActionResult(suppressed=True, reason="budget_exceeded", action_id=action.id)

    # 2b. PII filter for web_search
    pii_blocked, pii_reason = _check_pii_filter(action)
    if pii_blocked:
        action.state = "self_aborted"
        await audit.append(action)
        await _store_pii_refusal_memory(action, pii_reason)
        record_action(action.tool, action.verb, "self_aborted")
        record_held_back("pii_filter")
        log.info("gate_pii_blocked", action_id=action.id, reason=pii_reason)
        return ActionResult(suppressed=True, reason="pii_filter", action_id=action.id)

    # 3. Deliberation stub (always proceeds) — D-01

    # 4. Auth dispatch
    if action.authorization in ("free", "intimate"):
        return await _execute_and_record(action)

    if action.authorization == "kinetic":
        return await _execute_and_record(action)

    if action.authorization == "kinetic-sensitive":
        from chloe.actions.confirm import send as send_ticket
        action.state = "awaiting_confirmation"
        await audit.append(action)
        ticket = await send_ticket(action)
        record_action(action.tool, action.verb, "awaiting_confirmation")
        log.info("gate_awaiting_confirmation", action_id=action.id, ticket_id=ticket.id)
        return ActionResult(
            executed=False,
            suppressed=False,
            awaiting=True,
            ticket_id=ticket.id,
            action_id=action.id,
            reason=f"Awaiting confirmation (ticket {ticket.id})",
        )

    return ActionResult(suppressed=True, reason=f"unknown_auth: {action.authorization}")


async def _execute_and_record(action: Action) -> ActionResult:
    registry = get_registry()

    try:
        result = await registry.execute(action.tool, action.verb, action.args)
    except Exception as e:
        action.state = "failed"
        action.error = str(e)
        await audit.append(action)
        record_action(action.tool, action.verb, "failed")
        log.error("gate_execute_failed", action_id=action.id, error=str(e))
        return ActionResult(executed=False, error=str(e), action_id=action.id)

    if result.success:
        action.state = "executed"
        action.result = result.data or {}
        # C-06: create episodic memory for every successful action
        artifact_refs = []
        if result.artifact_ref:
            artifact_refs = [{"kind": result.artifact_kind or "unknown", "ref": result.artifact_ref}]
        memory_id = await _create_action_memory(action, artifact_refs)
        if memory_id is not None:
            action.becomes_memory_id = memory_id
    else:
        action.state = "failed"
        action.error = result.error or "unknown error"

    await audit.append(action)
    record_action(action.tool, action.verb, action.state)
    log.info("gate_executed", action_id=action.id, state=action.state)

    return ActionResult(
        executed=result.success,
        suppressed=False,
        action_id=action.id,
        error=action.error,
    )


async def _create_action_memory(action: Action, artifact_refs: list) -> int | None:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            INSERT INTO memories (kind, text, source, source_ref, artifact_refs, weight, tags, created_at)
            VALUES ('episodic', ?, 'action', ?, ?, 1.0, '["action"]', ?)
            """,
            (
                action.intent,
                action.id,
                json.dumps(artifact_refs),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        log.info("action_memory_created", memory_id=cursor.lastrowid, action_id=action.id)
        return cursor.lastrowid
    except Exception as exc:
        log.warning("action_memory_failed", error=str(exc))
        return None


def _check_pii_filter(action: Action) -> tuple[bool, str]:
    if action.tool != "web_search" or action.verb != "search":
        return False, ""
    try:
        from chloe.tools.web_search import _load_persons, sanitize
        query = action.args.get("query", "")
        persons = _load_persons()
        if not sanitize(query, persons):
            return True, f"PII detected in web_search query: '{query[:40]}'"
    except Exception as e:
        log.warning("pii_filter_error", error=str(e))
    return False, ""


async def _store_pii_refusal_memory(action: Action, reason: str) -> None:
    conn = get_connection()
    text = f"I almost searched for someone online. I stopped myself. Query hint: {action.args.get('query', '')[:20]}..."
    conn.execute(
        """
        INSERT INTO memories (kind, text, source, source_ref, tags, salience, confidence)
        VALUES ('episodic', ?, 'action', ?, '["held_back","refusal"]', 0.6, 1.0)
        """,
        (text, action.id),
    )
    conn.commit()


async def _store_held_back_memory(action: Action, reason: str) -> None:
    conn = get_connection()
    text = f"I almost {action.verb} via {action.tool}. Held back: {reason}. Intent: {action.intent}"
    conn.execute(
        """
        INSERT INTO memories (kind, text, source, source_ref, tags, salience)
        VALUES ('episodic', ?, 'action', ?, '["held_back"]', 0.4)
        """,
        (text, action.id),
    )
    conn.commit()
