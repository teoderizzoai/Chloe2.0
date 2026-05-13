"""Social graph — multi-person intelligence layer.

Handles mention extraction, cross-reference logging, per-person context
injection, and gen_level-gated impression modeling.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("persons.social_graph")

STANCE_KEYS = ("warmth_stance", "trust_stance", "interest_stance")


def upsert_mentioned_person(
    name: str,
    mentioned_by_id: int,
    content: str,
    emotional_valence: float = 0.0,
    confidentiality: str = "relational",
) -> int:
    """Ensure a person exists in the persons table, log a cross-reference.

    Returns the person id.
    """
    name = name.strip()[:80]
    if not name:
        return -1

    conn = get_connection()
    # Match by name OR by alias (aliases stored as JSON array, e.g. '["Zuzu","Zuza"]')
    row = conn.execute(
        """SELECT id, gen_level, relationship_class FROM persons
           WHERE name=? COLLATE NOCASE
              OR (aliases IS NOT NULL AND LOWER(aliases) LIKE ?)
           LIMIT 1""",
        (name, f'%"{name.lower()}"%'),
    ).fetchone()

    if row:
        pid = row["id"]
        # Bump gen_level if this is a repeated mention
        if row["gen_level"] == 0:
            conn.execute(
                "UPDATE persons SET gen_level=1, updated_at=? WHERE id=?",  # type: ignore[attr-defined]
                (datetime.now(timezone.utc).isoformat(), pid),
            )
    else:
        cur = conn.execute(
            """INSERT INTO persons (name, relationship_class, gen_level, confidentiality_default, created_at)
               VALUES (?, 'peripheral', 0, ?, ?)""",
            (name, confidentiality, datetime.now(timezone.utc).isoformat()),
        )
        pid = cur.lastrowid
        log.info("person_created_from_mention", name=name, id=pid)

    # Log the cross-reference
    conn.execute(
        """INSERT INTO person_cross_references
           (subject_id, mentioned_by, content, emotional_valence, confidentiality)
           VALUES (?, ?, ?, ?, ?)""",
        (pid, mentioned_by_id, content[:400], emotional_valence, confidentiality),
    )
    conn.commit()
    return pid


def process_social_mentions(
    mentions: list,
    mentioned_by_id: int,
) -> None:
    """Upsert each mentioned person and log cross-references."""
    for m in mentions:
        if isinstance(m, dict):
            name = m.get("name", "")
            content = m.get("content", "")
            valence = float(m.get("emotional_valence") or 0.0)
            conf = m.get("confidentiality", "relational")
        else:
            name = getattr(m, "name", "")
            content = getattr(m, "content", "")
            valence = float(getattr(m, "emotional_valence", 0.0) or 0.0)
            conf = getattr(m, "confidentiality", "relational")

        if not name.strip():
            continue
        try:
            upsert_mentioned_person(name, mentioned_by_id, content, valence, conf)
        except Exception as exc:
            log.warning("social_mention_failed", name=name, error=str(exc))


def load_person_context(person_id: int) -> dict:
    """Build per-person context for chat prompt injection.

    What gets injected is gated by gen_level:
      gen_level 0 — nothing (just a name in the DB)
      gen_level 1 — stance + recent cross-refs (impression forming)
      gen_level 2 — trait profile too (model exists)
      gen_level 3 — full model + addendum
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT name, gen_level, relationship_class, warmth_stance, trust_stance, interest_stance, trait_profile "
        "FROM persons WHERE id=?",
        (person_id,),
    ).fetchone()
    if not row:
        return {}

    gen_level = int(row["gen_level"] or 0)
    ctx: dict = {"name": row["name"], "gen_level": gen_level}

    if gen_level < 1:
        return ctx

    ctx["stance"] = {
        "warmth": round(float(row["warmth_stance"] or 0.5), 2),
        "trust": round(float(row["trust_stance"] or 0.5), 2),
        "interest": round(float(row["interest_stance"] or 0.5), 2),
    }

    xrefs = conn.execute(
        """SELECT content, emotional_valence, created_at FROM person_cross_references
           WHERE subject_id=? AND confidentiality != 'private'
           ORDER BY created_at DESC LIMIT 5""",
        (person_id,),
    ).fetchall()
    ctx["recent_mentions"] = [{"content": r["content"], "valence": r["emotional_valence"]} for r in xrefs]

    if gen_level >= 2:
        try:
            tp = row["trait_profile"] or "{}"
            ctx["impression"] = json.loads(tp) if isinstance(tp, str) else tp
        except Exception:
            ctx["impression"] = {}

    if gen_level >= 3:
        from chloe.identity.character_addendum import load_addendum
        ctx["addendum"] = load_addendum(person_id)

    return ctx


def format_person_context_for_prompt(person_id: int) -> str:
    """Return a formatted block for chat system prompt injection."""
    try:
        ctx = load_person_context(person_id)
    except Exception:
        return ""

    if not ctx or ctx.get("gen_level", 0) < 1:
        return ""

    name = ctx["name"]
    lines = [f"## About {name}"]

    stance = ctx.get("stance", {})
    if stance:
        warmth = stance.get("warmth", 0.5)
        trust = stance.get("trust", 0.5)
        interest = stance.get("interest", 0.5)
        lines.append(
            f"Your current read: warmth={warmth:.1f}, trust={trust:.1f}, interest={interest:.1f}"
        )

    mentions = ctx.get("recent_mentions", [])
    if mentions:
        lines.append("What you've heard about them lately:")
        for m in mentions[:3]:
            lines.append(f"- {m['content'][:150]}")

    impression = ctx.get("impression")
    if impression and ctx.get("gen_level", 0) >= 2:
        lines.append(f"Your impression: {json.dumps(impression)[:200]}")

    addendum = ctx.get("addendum")
    if addendum:
        lines.append(f"\n{addendum}")

    return "\n".join(lines)
