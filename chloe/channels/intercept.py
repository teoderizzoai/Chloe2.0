"""Message intercept layer.

Runs in parallel with the main chat reply. Two jobs:

1. Detect when Teo asked for something Chloe can't do. Queue a verb_proposals
   row so the next reflect can decide whether to draft a `define_verb` call.

2. Detect information worth holding onto (events, reminders, facts about
   people). Always enqueue a note to share_queue so Chloe will surface it
   in a later conversation; additionally, when the capture is action-shaped
   (e.g. a dentist appointment), submit a kinetic-sensitive action to the
   gate so Teo gets a confirm push before anything is written.

The intercept never produces user-visible text — the main reply still owns
the conversational response. Errors are swallowed and logged; the chat path
must keep working when this fails.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("channels.intercept")


async def run_intercept(user_text: str, person_id: str) -> None:
    """Top-level entry. Best-effort; never raises."""
    try:
        from chloe.llm.gemini import GeminiClient
        from chloe.llm.schemas import InterceptOutput

        client = GeminiClient()
        if not client._api_key:
            return  # silent: dev / sim contexts

        catalog = _tool_catalog()
        context = {
            "exchange": f"[teo, just now]: {user_text[:600]}",
            "now_iso": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "tool_catalog": catalog,
        }
        result = await client.flash("intercept_message.md", context, InterceptOutput)
        if not result:
            return

        data = result if isinstance(result, dict) else result.model_dump()
        pid = _resolve_pid(person_id)

        for req in data.get("requests", []) or []:
            try:
                _handle_request(req, pid, user_text)
            except Exception as exc:
                log.warning("intercept_request_failed", error=str(exc))

        for cap in data.get("captures", []) or []:
            try:
                await _handle_capture(cap, pid, user_text)
            except Exception as exc:
                log.warning("intercept_capture_failed", error=str(exc))

        log.info(
            "intercept_complete",
            requests=len(data.get("requests", []) or []),
            captures=len(data.get("captures", []) or []),
            confidence=round(float(data.get("confidence", 0.0)), 2),
        )
    except Exception as exc:
        log.warning("intercept_failed", error=str(exc))


def _resolve_pid(person_id: str) -> int:
    return int(person_id) if str(person_id).isdigit() else 1


def _tool_catalog() -> str:
    """Render the active tool/verb list for the intercept prompt.

    Kept short on purpose — the intercept only needs to know what exists,
    not full schemas. Description-for-human is the closest thing to a
    Teo-readable label.
    """
    from chloe.tools.registry import get_registry
    from chloe.config import FEATURE_FLAGS

    registry = get_registry()
    lines: list[str] = []
    for name, tool in registry._tools.items():
        if not FEATURE_FLAGS.get(name, True):
            continue
        verbs = [v for v in tool.verbs.keys()]
        if not verbs:
            continue
        lines.append(f"- {name}: {', '.join(verbs)}")
    for (tool_name, verb_name), row in registry._dynamic.items():
        if row.get("archived_at"):
            continue
        lines.append(f"- {tool_name}.{verb_name} (dynamic): {row.get('description', '')[:80]}")
    return "\n".join(lines) if lines else "(no tools registered)"


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

def _handle_request(req: dict, person_id: int, raw_text: str) -> None:
    """If the request flags a verb gap, queue a verb_proposals row.

    Matched requests are left alone — the main chat reply will dispatch the
    tool itself. The intercept is only responsible for the "no tool exists"
    branch.
    """
    if not req.get("verb_gap"):
        return

    tool_hint = (req.get("suggested_tool") or "").strip()[:40]
    verb_hint = (req.get("suggested_verb") or "").strip()[:40]
    intent = (req.get("text") or "").strip()[:400]
    rationale = (req.get("rationale") or "").strip()[:400]
    if not intent:
        return

    conn = get_connection()
    # Dedupe: if an identical pending proposal exists within 24h, skip.
    existing = conn.execute(
        """SELECT id FROM verb_proposals
           WHERE status='pending' AND intent=? AND created_at >= datetime('now', '-1 day')
           LIMIT 1""",
        (intent,),
    ).fetchone()
    if existing:
        log.info("verb_proposal_deduped", existing_id=existing["id"])
        return

    cur = conn.execute(
        """INSERT INTO verb_proposals
             (requested_text, tool_hint, verb_hint, intent, rationale,
              person_id, confidence, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (
            raw_text[:600],
            tool_hint or None,
            verb_hint or None,
            intent,
            rationale,
            person_id,
            0.6,
        ),
    )
    conn.commit()
    log.info(
        "verb_proposal_queued",
        id=cur.lastrowid,
        tool_hint=tool_hint,
        verb_hint=verb_hint,
    )


# ---------------------------------------------------------------------------
# Captures
# ---------------------------------------------------------------------------

async def _handle_capture(cap: dict, person_id: int, raw_text: str) -> None:
    """Two effects per capture: queue a share_queue note, and (if action-shaped)
    submit a kinetic-sensitive action through the gate so Teo gets a push.
    """
    summary = (cap.get("summary") or "").strip()
    if not summary:
        return

    domain = cap.get("domain") or "fact"
    when_iso = cap.get("when_iso")
    follow_up = cap.get("follow_up_question")
    person_name = cap.get("person_name")

    # 1. Share queue note — always.
    _enqueue_share_note(summary, domain, when_iso, person_name, follow_up, person_id)

    # 2. Action submission — only when concrete.
    action = cap.get("suggested_action")
    if not action or follow_up:
        # If the model itself flagged a follow-up, it doesn't have enough to
        # act on. Leave action alone; the share queue note will prompt Chloe.
        return

    tool = (action.get("tool") or "").strip()
    verb = (action.get("verb") or "").strip()
    args = action.get("args") or {}
    if not tool or not verb or not isinstance(args, dict):
        return

    await _submit_intercept_action(tool, verb, args, summary, person_id)


def _enqueue_share_note(
    summary: str,
    domain: str,
    when_iso: str | None,
    person_name: str | None,
    follow_up: str | None,
    person_id: int,
) -> None:
    """Put a note in share_queue so Chloe will bring it up later.

    Urgency leans higher when the intercept asked for a follow-up question
    (we want Chloe to circle back soon) or when the capture is time-bound.
    """
    from chloe.initiative.share_queue import enqueue

    parts = [summary]
    if when_iso:
        parts.append(f"(when: {when_iso})")
    if person_name:
        parts.append(f"(about: {person_name})")
    if follow_up:
        parts.append(f"\nWorth asking: {follow_up}")

    note = " ".join(parts).strip()[:800]
    urgency = 0.35 if follow_up else (0.25 if when_iso else 0.15)
    try:
        enqueue(
            content=note,
            source=f"intercept:{domain}",
            for_person_id=person_id,
            urgency=urgency,
        )
    except Exception as exc:
        log.warning("intercept_share_enqueue_failed", error=str(exc))


async def _submit_intercept_action(
    tool: str,
    verb: str,
    args: dict[str, Any],
    intent: str,
    person_id: int,
) -> None:
    """Build a kinetic-sensitive Action and pass it to the gate.

    We force kinetic-sensitive regardless of the verb's default — the intercept
    is making a guess based on a single message, so Teo confirms via push
    before anything writes.
    """
    from chloe.actions.schema import Action
    from chloe.actions import gate
    from chloe.tools.registry import get_registry

    registry = get_registry()
    verb_def = registry.get_verb(tool, verb)
    if verb_def is None:
        log.info("intercept_action_unknown_verb", tool=tool, verb=verb)
        return

    preview = _format_preview(tool, verb, args)
    action = Action(
        tool=tool,
        verb=verb,
        args=args,
        intent=f"intercept: {intent}"[:240],
        preview=preview,
        authorization="kinetic-sensitive",  # force confirm even if verb default is laxer
        origin="intercept",
        origin_person_id=person_id,
    )
    result = await gate.submit(action)
    log.info(
        "intercept_action_submitted",
        tool=tool, verb=verb,
        awaiting=bool(result.awaiting),
        suppressed=bool(result.suppressed),
        action_id=result.action_id,
    )


def _format_preview(tool: str, verb: str, args: dict[str, Any]) -> str:
    """Build a short human-readable preview for the confirm ticket UI."""
    if tool == "calendar" and verb == "add_event":
        title = args.get("title", "(no title)")
        start = args.get("start", "(no time)")
        return f"Add to calendar: '{title}' at {start}"
    if tool == "reminders" and verb == "add":
        body = args.get("body", "(no body)")
        time = args.get("time", "(no time)")
        return f"Reminder: '{body}' at {time}"
    # generic fallback
    keys = ", ".join(f"{k}={str(v)[:30]}" for k, v in args.items())
    return f"{tool}.{verb}({keys})"[:240]


# ---------------------------------------------------------------------------
# Verb proposal helpers used by reflect.
# ---------------------------------------------------------------------------

def get_pending_proposals(limit: int = 10) -> list[dict]:
    """Return unresolved verb_proposals for reflect to review."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, requested_text, tool_hint, verb_hint, intent,
                  rationale, person_id, confidence, created_at
           FROM verb_proposals
           WHERE status='pending'
           ORDER BY confidence DESC, created_at ASC LIMIT ?""",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_proposal(proposal_id: int, new_status: str, resulting_verb: str | None = None) -> None:
    """Move a proposal to dismissed or promoted."""
    if new_status not in ("dismissed", "promoted"):
        return
    conn = get_connection()
    if new_status == "promoted":
        conn.execute(
            """UPDATE verb_proposals
               SET status='promoted', promoted_at=datetime('now'),
                   resulting_verb=?, updated_at=datetime('now')
               WHERE id=?""",
            (resulting_verb, proposal_id),
        )
    else:
        conn.execute(
            "UPDATE verb_proposals SET status='dismissed', updated_at=datetime('now') WHERE id=?",
            (proposal_id,),
        )
    conn.commit()
