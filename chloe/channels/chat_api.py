from chloe.actions import audit
from chloe.affect.dims import load as load_affect, tone_block
from chloe.observability.logging import get_logger

log = get_logger("channels.chat_api")


async def build_dynamic_suffix(person_id: str, message: str = "") -> str:
    actions = await audit.recent(n=20)
    audit_text = audit.feed_text(actions, n=10) if actions else "No recent actions."

    affect = load_affect()
    affect_text = tone_block(affect)

    parts = [
        f"## Recent actions\n{audit_text}",
        f"## Current affect\n{affect_text}",
    ]

    # E-09: memory retrieval — inject graded memories when a message is present
    if message:
        mem_block = await _fetch_memory_block(message, affect)
        if mem_block:
            parts.append(f"## Relevant memories\n{mem_block}")

    # E-11: relationship prose label for the person
    rel_label = _relationship_label_for(person_id)
    if rel_label:
        parts.append(f"## Relationship context\n{rel_label}")

    return "\n\n".join(parts)


async def _fetch_memory_block(message: str, affect) -> str:
    try:
        from chloe.memory import retrieval
        from chloe.memory import store as mem_store

        candidates = retrieval.query_mixed(message)
        if not candidates:
            return ""
        graded = await mem_store.grade(candidates, message, [], affect, keep=5)
        if not graded:
            return ""
        return "\n".join(f"- {m.text}" for m in graded)
    except Exception as exc:
        log.warning("memory_fetch_failed", error=str(exc))
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
