from __future__ import annotations

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("inside_jokes")

INSIDE_JOKE_WEIGHT = 0.85
RETRIEVAL_BONUS = 0.12


async def record_inside_joke(topic: str, context_snippet: str) -> str | None:
    """
    Create or reinforce an inside-joke memory for the given topic.
    Returns memory_id (str) if created, None if reinforced.
    """
    existing = _find_existing(topic)

    if existing:
        new_weight = min(1.0, existing["weight"] + 0.05)
        _set_weight(existing["id"], new_weight)
        log.info("inside_joke_reinforced", topic=topic, memory_id=existing["id"])
        return None

    from chloe.memory import store as memory_store
    tags = ["inside_joke", "semantic", f"joke_topic:{topic}"]
    memory_id = memory_store.add(
        kind="semantic",
        text=f"Inside reference with Teo: '{topic}'. ({context_snippet[:120]})",
        source="humor_detection",
        tags=tags,
        weight=INSIDE_JOKE_WEIGHT,
    )
    log.info("inside_joke_created", topic=topic, memory_id=memory_id)
    return str(memory_id)


def _find_existing(topic: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT id, weight FROM memories WHERE tags LIKE ? AND kind='semantic'",
        (f"%joke_topic:{topic}%",),
    ).fetchone()
    if row is None:
        return None
    return {"id": row["id"], "weight": row["weight"]}


def _set_weight(memory_id: int, weight: float) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE memories SET weight = ? WHERE id = ?",
        (weight, memory_id),
    )
    conn.commit()
