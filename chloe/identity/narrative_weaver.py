"""Narrative Weaver — weekly Opus call that produces a NarrativeEntry.

After trait adjudication and self-model run, reads 30 days of episodic
memories, current trait state, interests, and world beliefs to write
one honest timeline entry.

If chapter_transition=True, also triggers character addendum generation.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("identity.narrative_weaver")

CHARACTER_ADDENDUM_EVERY_DAYS = 30


class InterestPromotion(BaseModel):
    interest_id: int
    new_level: int = Field(ge=1, le=3)
    reason: str = Field(max_length=200, default="")


class NarrativeEntry(BaseModel):
    period_label: str = Field(max_length=120)
    what_happened: str = Field(max_length=800)
    what_shifted: str = Field(max_length=400, default="")
    still_sitting_with: str = Field(max_length=300, default="")
    felt_texture: str = Field(max_length=120, default="")
    chapter_transition: bool = False
    interest_promotions: list[InterestPromotion] = Field(default_factory=list)


class AddendumOutput(BaseModel):
    addendum: str = Field(max_length=1000)


async def weave_narrative() -> dict:
    from chloe.llm.gemini import GeminiClient
    from chloe.identity.character_addendum import update_addendum

    conn = get_connection()
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).date().isoformat()

    payload = {
        "week_start": week_start,
        "episodic_memories": _load_episodic_memories(conn, days=30),
        "action_audit": _load_action_audit(conn, days=7),
        "trait_state": _load_traits(conn),
        "interest_garden": _load_interests(conn),
        "world_beliefs": _load_beliefs(conn),
        "unprocessed_summary": _load_unprocessed_summary(conn),
        "previous_entry": _load_previous_entry(conn),
    }

    client = GeminiClient()
    result = await client.pro_thinking("narrative_weaver.md", payload, NarrativeEntry)

    if not result:
        log.warning("narrative_weaver_returned_none")
        return {"error": "llm_returned_none"}

    entry = NarrativeEntry(**result) if isinstance(result, dict) else result

    # Persist to narrative_timeline
    conn.execute(
        """INSERT INTO narrative_timeline
           (period_label, what_happened, what_shifted, still_sitting_with, felt_texture, chapter_transition, week_start)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            entry.period_label, entry.what_happened, entry.what_shifted,
            entry.still_sitting_with, entry.felt_texture,
            1 if entry.chapter_transition else 0, week_start,
        ),
    )
    conn.commit()
    log.info("narrative_entry_written", label=entry.period_label, chapter=entry.chapter_transition)

    # Apply interest promotions from the narrative model
    from chloe.identity.interest_garden import promote_interest
    for promo in entry.interest_promotions:
        promoted = promote_interest(promo.interest_id, promo.new_level, evidence=promo.reason)
        log.info("narrative_interest_promotion", id=promo.interest_id,
                 level=promo.new_level, success=promoted)

    addendum_triggered = False
    if entry.chapter_transition or _addendum_overdue(conn):
        try:
            await update_addendum(
                person_id=None,
                narrative_context=f"{entry.period_label}: {entry.what_happened}",
            )
            addendum_triggered = True
        except Exception as exc:
            log.warning("addendum_update_failed", error=str(exc))

    return {
        "period_label": entry.period_label,
        "chapter_transition": entry.chapter_transition,
        "felt_texture": entry.felt_texture,
        "addendum_triggered": addendum_triggered,
    }


def _load_episodic_memories(conn, days: int = 30) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT text, created_at FROM memories
           WHERE salience > 0.4 AND unprocessed=0 AND created_at >= ?
           ORDER BY salience DESC, created_at DESC LIMIT 30""",
        (cutoff,),
    ).fetchall()
    if not rows:
        return "(no notable memories)"
    return "\n".join(f"- [{r['created_at'][:10]}] {r['text'][:200]}" for r in rows)


def _load_action_audit(conn, days: int = 7) -> str:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT text, created_at FROM memories
           WHERE source='action' AND created_at >= ?
           ORDER BY created_at DESC LIMIT 20""",
        (cutoff,),
    ).fetchall()
    if not rows:
        return "(no autonomous actions)"
    return "\n".join(f"- {r['text'][:200]}" for r in rows)


def _load_traits(conn) -> str:
    rows = conn.execute(
        "SELECT name, weight, gen_level, status FROM identity_traits WHERE status NOT IN ('archived') ORDER BY weight DESC LIMIT 10"
    ).fetchall()
    if not rows:
        return "(no traits observed yet)"
    return "\n".join(
        f"- {r['name']} (weight={r['weight']:.2f}, gen={r['gen_level']}, {r['status']})"
        for r in rows
    )


def _load_interests(conn) -> str:
    rows = conn.execute(
        "SELECT id, label, intensity, gen_level FROM interest_garden WHERE intensity > 0 ORDER BY intensity DESC LIMIT 10"
    ).fetchall()
    if not rows:
        return "(none)"
    return "\n".join(
        f"- id={r['id']} {r['label']} ({r['intensity']:.2f}, gen={r['gen_level']})"
        for r in rows
    )


def _load_beliefs(conn) -> str:
    rows = conn.execute(
        "SELECT topic, belief, confidence FROM world_beliefs ORDER BY confidence DESC LIMIT 10"
    ).fetchall()
    if not rows:
        return "(none yet)"
    return "\n".join(f"- [{r['confidence']:.2f}] {r['topic']}: {r['belief']}" for r in rows)


def _load_unprocessed_summary(conn) -> str:
    row = conn.execute(
        "SELECT COUNT(*) AS n, MIN(created_at) AS oldest FROM memories WHERE unprocessed=1"
    ).fetchone()
    if not row or not row["n"]:
        return "0 unprocessed memories"
    return f"{row['n']} unprocessed memories; oldest from {(row['oldest'] or '')[:10]}"


def _load_previous_entry(conn) -> str:
    row = conn.execute(
        "SELECT period_label, what_happened, felt_texture FROM narrative_timeline ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return "(no previous entry)"
    return f"Last period: \"{row['period_label']}\" — {row['what_happened'][:200]}"


def _addendum_overdue(conn) -> bool:
    row = conn.execute(
        "SELECT created_at FROM character_addenda ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not row:
        return True
    try:
        last_dt = datetime.fromisoformat(row["created_at"])
        return (datetime.now(timezone.utc) - last_dt) >= timedelta(days=CHARACTER_ADDENDUM_EVERY_DAYS)
    except Exception:
        return True
