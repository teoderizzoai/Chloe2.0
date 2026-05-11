import asyncio
from datetime import datetime, timezone, timedelta

from chloe.actions import audit
from chloe.affect.dims import load as load_affect, tone_block
from chloe.observability.logging import get_logger

log = get_logger("channels.chat_api")

_GAP_THRESHOLD_HOURS = 4


async def build_dynamic_suffix(person_id: str, message: str = "") -> str:
    # Run the three independent prep tasks concurrently. Previously sequential:
    # audit -> affect -> retrieval -> grade(LLM!) — saved ~1–2s by dropping
    # the grade Flash call and parallelizing the rest.
    affect = load_affect()
    actions_task = audit.recent(n=20)
    mem_task = _fetch_memory_block(message) if message else _noop()

    actions, mem_block = await asyncio.gather(actions_task, mem_task)

    audit_text = audit.feed_text(actions, n=10) if actions else "No recent actions."
    affect_text = tone_block(affect)

    parts = [
        f"## Recent actions\n{audit_text}",
        f"## Current affect\n{affect_text}",
    ]
    if mem_block:
        parts.append(f"## Relevant memories\n{mem_block}")

    rel_label = _relationship_label_for(person_id)
    if rel_label:
        parts.append(f"## Relationship context\n{rel_label}")

    addendum = _load_character_addendum(person_id)
    if addendum:
        parts.append(f"## How you are with this person right now\n{addendum}")

    person_ctx = _load_social_person_context(person_id)
    if person_ctx:
        parts.append(person_ctx)

    self_model = _load_self_model()
    if self_model:
        parts.append(f"## What you believe about yourself\n{self_model}")

    world_views = _load_world_beliefs()
    if world_views:
        parts.append(f"## Views you hold about the world\n{world_views}")

    gap_note = _felt_time_note()
    if gap_note:
        parts.append(f"## Time since last conversation\n{gap_note}")

    unprocessed = _load_unprocessed_block()
    if unprocessed:
        parts.append(f"## Things you haven't fully made sense of yet\n{unprocessed}")

    felt = _felt_state_block()
    if felt:
        parts.append(f"## Your current felt state\n{felt}")

    voice_note = _load_voice_drift_note()
    if voice_note:
        parts.append(f"## What to recalibrate this week\n{voice_note}")

    return "\n\n".join(parts)


async def _noop() -> str:
    return ""


async def _fetch_memory_block(message: str, keep: int = 5) -> str:
    """Top-K by Chroma score, no LLM rerank.

    The previous implementation called Gemini Flash to grade ~20 candidates
    down to 5 — adding a full LLM round-trip (~1–2s) on every chat turn.
    For a chat prompt, raw Chroma score ordering is good enough. The grader
    is still used in deliberation paths where higher precision matters.
    """
    try:
        from chloe.memory import retrieval

        candidates = retrieval.query_fast(message, n=keep * 2)
        if not candidates:
            return ""
        top = candidates[:keep]
        return "\n".join(f"- {m.text}" for m in top)
    except Exception as exc:
        log.warning("memory_fetch_failed", error=str(exc))
        return ""


def _load_self_model() -> str:
    """Return the most recent self-narrative belief from the weekly self-model."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT text FROM inner_beliefs WHERE archived=0 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return row["text"].strip() if row else ""
    except Exception:
        return ""


def _felt_time_note() -> str:
    """Return a note about how long it's been since the last conversation, if > threshold."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT created_at FROM memories WHERE source='chat' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return ""
        last_dt = datetime.fromisoformat(row["created_at"])
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        gap = datetime.now(timezone.utc) - last_dt
        hours = gap.total_seconds() / 3600
        if hours < _GAP_THRESHOLD_HOURS:
            return ""
        if hours < 24:
            return f"It's been about {int(hours)} hours since your last exchange. You've been thinking in the gap."
        days = int(hours / 24)
        return f"It's been about {days} day{'s' if days != 1 else ''} since your last exchange. Notice the texture of that gap."
    except Exception:
        return ""


def _load_world_beliefs(limit: int = 5) -> str:
    """Return top world beliefs by confidence for injection into the chat prompt.

    Phrasing reflects developmental stage of the belief:
      - confidence < 0.4              → "something that might be true"
      - confidence 0.4 ≤ x ≤ 0.65     → "something you've started to think"
      - confidence > 0.65             → "something you believe"
    Noticings (proto-beliefs, not yet beliefs) are surfaced with even softer
    language. Capped at `limit` rows regardless of total count.
    """
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """SELECT topic, belief, confidence, noticing
               FROM world_beliefs
               ORDER BY confidence DESC, updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        if not rows:
            return ""
        groups: dict[str, list[str]] = {
            "noticings": [],
            "low": [],
            "mid": [],
            "high": [],
        }
        for r in rows:
            conf = float(r["confidence"] or 0.0)
            line = f"- {r['topic']}: {r['belief']}"
            if r["noticing"]:
                groups["noticings"].append(line)
            elif conf < 0.4:
                groups["low"].append(line)
            elif conf <= 0.65:
                groups["mid"].append(line)
            else:
                groups["high"].append(line)

        sections: list[str] = []
        if groups["high"]:
            sections.append(
                "Things you believe and should be willing to bring into conversation "
                "(if the topic comes up, say where you stand — don't stay neutral):\n"
                + "\n".join(groups["high"])
            )
        if groups["mid"]:
            sections.append("Things you've started to think:\n" + "\n".join(groups["mid"]))
        if groups["low"]:
            sections.append("Things that might be true:\n" + "\n".join(groups["low"]))
        if groups["noticings"]:
            sections.append("Things you've noticed but aren't sure about yet:\n" + "\n".join(groups["noticings"]))
        return "\n\n".join(sections)
    except Exception:
        return ""


def _load_social_person_context(person_id: str) -> str:
    """Return formatted social context for people mentioned by this person."""
    try:
        pid = int(person_id)
        from chloe.state.db import get_connection
        from chloe.persons.social_graph import format_person_context_for_prompt
        conn = get_connection()
        xref_subjects = conn.execute(
            """SELECT DISTINCT subject_id FROM person_cross_references
               WHERE mentioned_by=? AND subject_id != ?
               ORDER BY created_at DESC LIMIT 5""",
            (pid, pid),
        ).fetchall()
        if not xref_subjects:
            return ""
        blocks = []
        for row in xref_subjects:
            block = format_person_context_for_prompt(row["subject_id"])
            if block:
                blocks.append(block)
        return "\n\n".join(blocks) if blocks else ""
    except Exception:
        return ""


def _load_character_addendum(person_id: str) -> str:
    try:
        pid = int(person_id)
        from chloe.identity.character_addendum import load_addendum
        return load_addendum(pid)
    except Exception:
        return ""


def _load_unprocessed_block(limit: int = 3) -> str:
    """Surface unresolved high-salience memories without forcing interpretation."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """SELECT text FROM memories
               WHERE unprocessed=1
               ORDER BY salience DESC, created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        if not rows:
            return ""
        return "\n".join(f"- {r['text'][:200]}" for r in rows)
    except Exception:
        return ""


def _felt_state_block() -> str:
    """Return a felt-state phrase if one is cached, else empty.

    Generation happens lazily in chloe/affect/dims.felt_state_phrase().
    """
    try:
        from chloe.affect.dims import felt_state_phrase
        phrase = felt_state_phrase()
        return phrase or ""
    except Exception:
        return ""


def _load_voice_drift_note(max_age_days: int = 14) -> str:
    """Return the most recent voice drift note if it's less than max_age_days old.

    The note is a sentence from the weekly self-model about what Chloe should
    recalibrate in her voice this week. Injects as a small prompt block only
    when fresh enough to matter.
    """
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            """SELECT note, created_at FROM voice_drift_log
               WHERE created_at >= datetime('now', ?)
               ORDER BY created_at DESC LIMIT 1""",
            (f"-{max_age_days} days",),
        ).fetchone()
        return row["note"].strip() if row else ""
    except Exception:
        return ""


def _relationship_label_for(person_id: str) -> str:
    try:
        pid = int(person_id)
        from chloe.state.db import get_connection
        from chloe.persons.attachment import relationship_label
        conn = get_connection()
        row = conn.execute(
            "SELECT name, attachment_depth FROM persons WHERE id = ?", (pid,)
        ).fetchone()
        if row is None:
            return ""
        name = row["name"]
        depth = row["attachment_depth"]
        label = relationship_label(depth)
        return f"With {name}: {label} (depth {round(depth, 2)})"
    except Exception:
        return ""
