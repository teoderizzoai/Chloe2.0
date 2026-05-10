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

    self_model = _load_self_model()
    if self_model:
        parts.append(f"## What you believe about yourself\n{self_model}")

    world_views = _load_world_beliefs()
    if world_views:
        parts.append(f"## Views you hold about the world\n{world_views}")

    gap_note = _felt_time_note()
    if gap_note:
        parts.append(f"## Time since last conversation\n{gap_note}")

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
    """Return top world beliefs by confidence for injection into the chat prompt."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT topic, belief FROM world_beliefs ORDER BY confidence DESC, updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        if not rows:
            return ""
        return "\n".join(f"- {r['topic']}: {r['belief']}" for r in rows)
    except Exception:
        return ""


def _relationship_label_for(person_id: str) -> str:
    try:
        pid = int(person_id) if str(person_id).isdigit() else None
        if pid is None:
            return ""
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
