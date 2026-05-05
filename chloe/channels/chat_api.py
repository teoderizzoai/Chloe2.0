from chloe.actions import audit
from chloe.state import kv


async def build_dynamic_suffix(person_id: str) -> str:
    actions = await audit.recent(n=20)
    audit_text = audit.feed_text(actions, n=10) if actions else "No recent actions."

    affect_text = kv.get("mood_label") or "unknown"

    parts = [
        f"## Recent actions\n{audit_text}",
        f"## Current affect\n{affect_text}",
    ]
    return "\n\n".join(parts)
