"""Aesthetic reaction log and monthly pattern recognition.

Reactions are logged per-stimulus. Pattern analysis runs monthly after
90 days of data, producing behavioral descriptions stored in kv.
These patterns shape the character addendum, NOT the chat prompt directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Literal

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("identity.aesthetics")

Domain = Literal["music", "language", "image", "idea", "space", "unknown"]

DOMAINS: list[str] = ["music", "language", "image", "idea", "space"]
MIN_REACTIONS_FOR_PATTERN = 10


def log_reaction(
    stimulus: str,
    domain: Domain = "unknown",
    valence: float = 0.0,
    intensity: float = 0.5,
    notes: str = "",
) -> int:
    """Record one aesthetic reaction to a specific stimulus. Returns id."""
    stimulus = stimulus.strip()[:400]
    notes = notes.strip()[:500]
    valence = max(-1.0, min(1.0, valence))
    intensity = max(0.0, min(1.0, intensity))

    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO aesthetic_reactions (stimulus, domain, valence, intensity, notes, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (stimulus, domain, valence, intensity, notes, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    log.info("aesthetic_reaction_logged", id=cur.lastrowid, domain=domain, intensity=intensity)
    return cur.lastrowid


def first_reaction_age_days() -> float | None:
    """Return days since the first reaction was logged, or None if none exist."""
    conn = get_connection()
    row = conn.execute("SELECT MIN(created_at) AS oldest FROM aesthetic_reactions").fetchone()
    if not row or not row["oldest"]:
        return None
    try:
        dt = datetime.fromisoformat(row["oldest"])
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
    except Exception:
        return None


async def run_pattern_review() -> dict:
    """Monthly Flash call to find behavioral patterns in the reaction log.

    Only runs after 90 days of data. Patterns are stored in kv, not the
    chat prompt — they flow into the character addendum at next generation.
    """
    from chloe.llm.gemini import GeminiClient
    from chloe.state.kv import set as kv_set
    from pydantic import BaseModel, Field

    class PatternItem(BaseModel):
        domain: str = Field(max_length=20)
        pattern: str = Field(max_length=200)
        confidence: float = Field(ge=0.0, le=1.0, default=0.5)
        evidence_count: int = Field(ge=0, default=0)

    class PatternsOutput(BaseModel):
        patterns: list[PatternItem] = Field(default_factory=list)
        notes: str = Field(max_length=200, default="")
        orientation: str = Field(
            max_length=400,
            default="",
            description="2-3 sentences about what she's drawn toward generatively — not reactive patterns, but what she would seek out if the choice were hers.",
        )

    conn = get_connection()
    domain_blocks: dict[str, list[str]] = {d: [] for d in DOMAINS}

    for domain in DOMAINS:
        rows = conn.execute(
            """SELECT stimulus, valence, intensity, notes FROM aesthetic_reactions
               WHERE domain=? ORDER BY created_at DESC LIMIT 100""",
            (domain,),
        ).fetchall()
        for r in rows:
            line = f"valence={r['valence']:+.1f} intensity={r['intensity']:.1f}: {r['stimulus'][:120]}"
            if r["notes"]:
                line += f" — {r['notes'][:80]}"
            domain_blocks[domain].append(line)

    reactions_text = "\n\n".join(
        f"### {d.upper()} ({len(lines)} reactions)\n" + "\n".join(lines[:30])
        for d, lines in domain_blocks.items() if lines
    )

    if not reactions_text:
        return {"skipped": True, "reason": "no_reactions"}

    client = GeminiClient()
    result = await client.flash("aesthetic_patterns.md", {"reactions_by_domain": reactions_text}, PatternsOutput)

    if not result:
        return {"error": "llm_returned_none"}

    output = PatternsOutput(**result) if isinstance(result, dict) else result

    # Store per-domain in kv
    by_domain: dict[str, list[dict]] = {}
    for p in output.patterns:
        by_domain.setdefault(p.domain, []).append({
            "pattern": p.pattern,
            "confidence": p.confidence,
            "evidence_count": p.evidence_count,
        })

    for domain, patterns in by_domain.items():
        kv_set(f"aesthetic_pattern:{domain}", patterns)

    # Store aesthetic orientation separately — injected into chat prompt, not just addendum
    if output.orientation and output.orientation.strip():
        kv_set("identity:aesthetic_orientation", output.orientation.strip())

    log.info("aesthetic_patterns_stored", domains=list(by_domain.keys()), total=len(output.patterns))
    return {
        "patterns": len(output.patterns),
        "domains": list(by_domain.keys()),
        "notes": output.notes,
        "orientation": bool(output.orientation),
    }


def load_patterns_for_addendum() -> str:
    """Return aesthetic patterns formatted for injection into character addendum prompt."""
    from chloe.state.kv import get as kv_get
    lines = []
    for domain in DOMAINS:
        raw = kv_get(f"aesthetic_pattern:{domain}")
        if not raw:
            continue
        try:
            patterns = raw if isinstance(raw, list) else json.loads(raw)
            for p in patterns:
                lines.append(f"[{domain}] {p.get('pattern', '')}")
        except Exception:
            pass
    return "\n".join(lines) if lines else ""
