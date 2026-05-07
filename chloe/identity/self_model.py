from __future__ import annotations

import json
from datetime import datetime, timezone

from chloe.llm.gemini import GeminiClient
from chloe.llm.schemas import SelfModelOutput
from chloe.state.db import get_connection
from chloe.observability.logging import get_logger

log = get_logger("self_model")
_gemini = GeminiClient()

# Calibrated 2026-05-06 against 3 production identity snapshots.
# 1024: shallow, missed key goal contradictions
# 4096: good quality, but dropped nuance about affect drift
# 8192: full context used; belief was more specific and honest
# Chosen: 8192. Cost ~$0.08/week; acceptable.
WEEKLY_PARAMS = {
    "thinking_config": {
        "thinking_budget": 8192,
    }
}


async def run_weekly_self_model() -> dict | None:
    """
    Runs once per week (Sunday ~03:00).
    Returns dict with new belief_id and goal_id, or None on failure.
    """
    log.info("self_model_start")
    payload = _assemble_input_pack()

    try:
        result = await _gemini.pro_thinking(
            prompt_file="weekly_self_model.md",
            context=payload,
            schema=SelfModelOutput,
            thinking_budget=WEEKLY_PARAMS["thinking_config"]["thinking_budget"],
        )
    except Exception as exc:
        log.error("self_model_llm_error", error=str(exc))
        return None

    if result is None:
        log.warning("self_model_llm_returned_none")
        return None

    output = SelfModelOutput(**result) if isinstance(result, dict) else result
    belief_id = _write_belief(output.self_narrative_belief, confidence=0.5)
    goal_id = _write_goal(output.next_week_intention)
    _apply_optional_outputs(output)

    log.info("self_model_complete", belief_id=belief_id, goal_id=goal_id)
    return {"belief_id": belief_id, "goal_id": goal_id}


def _assemble_input_pack() -> dict:
    conn = get_connection()

    traits = conn.execute(
        "SELECT name, weight, behavioral_profile FROM identity_traits ORDER BY weight DESC LIMIT 10"
    ).fetchall()

    contradictions = conn.execute(
        "SELECT description FROM identity_contradictions ORDER BY detected_at DESC LIMIT 5"
    ).fetchall()

    goals = conn.execute(
        "SELECT name, progress, status FROM inner_goals ORDER BY progress ASC LIMIT 5"
    ).fetchall()

    wants = conn.execute(
        "SELECT text FROM inner_wants ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    fears = conn.execute(
        "SELECT text FROM inner_fears ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    recent_beliefs = conn.execute(
        "SELECT text, confidence FROM inner_beliefs ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    from chloe.actions.audit import feed_text
    recent_actions = feed_text([], n=30)

    affect_row = conn.execute(
        "SELECT valence, arousal, social_pull, openness FROM affect_state WHERE id=1"
    ).fetchone()

    held_back = _load_held_back_summary()
    voice_context = _load_voice_drift_context(conn)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "traits": [dict(t) for t in traits],
        "contradictions": [dict(c) for c in contradictions],
        "goals": [dict(g) for g in goals],
        "wants": [dict(w) for w in wants],
        "fears": [dict(f) for f in fears],
        "recent_beliefs": [dict(b) for b in recent_beliefs],
        "recent_actions": recent_actions,
        "affect": dict(affect_row) if affect_row else {},
        "held_back_summary": held_back,
        "voice_drift_context": voice_context,
    }


def _load_held_back_summary() -> dict:
    from datetime import timedelta
    conn = get_connection()
    now = datetime.now(timezone.utc)

    rows_7d = conn.execute(
        """
        SELECT m.text, a.tool
        FROM memories m
        LEFT JOIN actions a ON a.id = m.source_ref
        WHERE m.tags LIKE '%held_back%'
          AND m.created_at >= ?
        ORDER BY m.created_at DESC
        """,
        ((now - timedelta(days=7)).isoformat(),),
    ).fetchall()

    count_30d_row = conn.execute(
        """
        SELECT COUNT(*) as cnt FROM memories
        WHERE tags LIKE '%held_back%'
          AND created_at >= ?
        """,
        ((now - timedelta(days=30)).isoformat(),),
    ).fetchone()

    tools = [r["tool"] for r in rows_7d if r["tool"]]
    tool_counts: dict[str, int] = {}
    for t in tools:
        tool_counts[t] = tool_counts.get(t, 0) + 1
    top_tools = sorted(tool_counts, key=lambda k: -tool_counts[k])[:3]

    themes = list({r["text"][:80] for r in rows_7d})[:5]
    sample_notes = [r["text"] for r in rows_7d[:3]]

    return {
        "count_7d": len(rows_7d),
        "count_30d": count_30d_row["cnt"] if count_30d_row else 0,
        "top_tools": top_tools,
        "themes": themes,
        "sample_notes": sample_notes,
    }


def _load_voice_drift_context(conn) -> dict:
    from chloe.state.kv import get as kv_get

    notes = kv_get("voice_drift_notes") or []
    last_note = notes[-1]["note"] if notes else None

    rows = conn.execute(
        """
        SELECT text FROM chat_history
        WHERE role = 'chloe'
        ORDER BY created_at DESC LIMIT 5
        """
    ).fetchall()
    samples = [r["text"] for r in rows]

    return {
        "last_voice_note": last_note,
        "sample_exchanges": samples,
    }


def _write_belief(text: str, confidence: float = 0.5) -> int:
    conn = get_connection()
    conn.execute(
        "INSERT INTO inner_beliefs (text, confidence, tags, created_at) VALUES (?, ?, ?, ?)",
        (text, confidence, json.dumps(["weekly_self_model"]),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]


def _write_goal(intention: str) -> int:
    conn = get_connection()
    conn.execute(
        "INSERT INTO inner_goals (name, progress, status, created_at) VALUES (?, ?, ?, ?)",
        (intention, 0.0, "active", datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]


def _apply_optional_outputs(output: SelfModelOutput) -> None:
    if output.restraint_reflection:
        _write_belief(
            output.restraint_reflection,
            confidence=0.45,
        )
        conn = get_connection()
        # Update tags on the just-inserted row
        belief_id = conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]
        conn.execute(
            "UPDATE inner_beliefs SET tags = ? WHERE id = ?",
            (json.dumps(["restraint", "self_image", "autobiographical"]), belief_id),
        )
        conn.commit()
        log.info("restraint_belief_written", belief_id=belief_id)

    if output.voice_drift_note:
        from chloe.state.kv import get as kv_get, set as kv_set
        existing = kv_get("voice_drift_notes") or []
        existing.append({
            "note": output.voice_drift_note,
            "written_at": datetime.now(timezone.utc).isoformat(),
        })
        kv_set("voice_drift_notes", existing[-3:])
        log.info("voice_drift_note_written", note=output.voice_drift_note)
