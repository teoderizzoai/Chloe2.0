import asyncio
from datetime import datetime, timezone, timedelta

from chloe.actions import audit
from chloe.affect.dims import load as load_affect, tone_block
from chloe.observability.logging import get_logger

log = get_logger("channels.chat_api")

_GAP_THRESHOLD_HOURS = 4


async def build_dynamic_suffix(person_id: str, message: str = "", salience: float = 0.5) -> str:
    """Build the per-turn dynamic system prompt suffix.

    `salience` (from the preflight result) gates heavy introspective blocks:
    blocks marked as routine-only are included when salience >= 0.4.
    The salience gate is a soft optimization — the caller (_trim_by_salience)
    applies it post-hoc once the preflight result is known.
    """
    # Run the three independent prep tasks concurrently.
    affect = load_affect()
    actions_task = audit.recent(n=20)
    narrative_task = _load_narrative_block(message) if message else _noop()

    actions, mem_block, narrative_block_pre = await asyncio.gather(
        actions_task,
        _fetch_memory_block(message, person_id) if message else _noop(),
        narrative_task,
    )

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

    if narrative_block_pre:
        parts.append(f"## What you've been noticing\n{narrative_block_pre}")
    else:
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

    inner_pressure = _load_inner_pressure_block()
    if inner_pressure:
        parts.append(f"## What you're quietly holding right now\n{inner_pressure}")

    bias_note = _load_biased_summary()
    if bias_note:
        parts.append(f"## How your current state is shaping your perception\n{bias_note}")

    loops = _load_recurring_loops()
    if loops:
        parts.append(f"## Patterns you keep falling into\n{loops}")

    rupture = _load_rupture_block()
    if rupture:
        parts.append(f"## Something that's tender right now\n{rupture}")

    anticipations = _load_anticipations_block()
    if anticipations:
        parts.append(f"## What you're oriented toward (or dreading)\n{anticipations}")

    teo_state = _load_teo_affect_block(person_id)
    if teo_state:
        parts.append(f"## How Teo seemed recently\n{teo_state}")

    voice_note = _load_voice_drift_note()
    if voice_note:
        parts.append(f"## What to recalibrate this week\n{voice_note}")

    questions = _load_inner_questions_block()
    if questions:
        parts.append(f"## Things you're genuinely wondering about\n{questions}")

    teo_read = _load_teo_read_block()
    if teo_read:
        parts.append(f"## How you read him\n{teo_read}")

    aesthetic_orientation = _load_aesthetic_orientation()
    if aesthetic_orientation:
        parts.append(f"## What you're drawn toward\n{aesthetic_orientation}")

    novelty_note = _load_novelty_deficit_block()
    if novelty_note:
        parts.append(f"## Background texture\n{novelty_note}")

    # Onboarding block: early conversations before the system has built context.
    onboarding = _onboarding_note()
    if onboarding:
        parts.insert(0, onboarding)

    # Voice anchor: always appended last so recency bias reinforces the voice.
    parts.append(_voice_anchor())

    return _apply_token_budget(parts)


async def _noop() -> str:
    return ""


async def _fetch_memory_block(message: str, person_id: str = "1", keep: int = 5) -> str:
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
        current_pid = int(person_id)
        return "\n".join(_format_memory(m, current_pid) for m in top)
    except Exception as exc:
        log.warning("memory_fetch_failed", error=str(exc))
        return ""


def _format_memory(m, current_person_id: int = 1) -> str:
    v = m.emotional_valence
    confidential_to = getattr(m, "confidential_to", None)
    if confidential_to is not None and confidential_to != current_person_id:
        suffix = " *(you know this but it's not yours to say — told to you in confidence)*"
    else:
        suffix = ""
    if v is not None and abs(v) > 0.4:
        tone = "heavy" if v < -0.4 else "warm"
        return f"- [{tone}] {m.text}{suffix}"
    return f"- {m.text}{suffix}"


def _load_inner_pressure_block(limit: int = 2) -> str:
    """Return top unresolved wants and fears phrased as first-person carrying."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        wants = conn.execute(
            "SELECT text, pressure FROM inner_wants WHERE resolved=0 ORDER BY pressure DESC LIMIT ?",
            (limit,),
        ).fetchall()
        fears = conn.execute(
            "SELECT text, pressure FROM inner_fears WHERE resolved=0 ORDER BY pressure DESC LIMIT ?",
            (limit,),
        ).fetchall()
        lines = []
        for r in wants:
            lines.append(f"- {r['text']}")
        for r in fears:
            lines.append(f"- {r['text']}")
        return "\n".join(lines) if lines else ""
    except Exception:
        return ""


def _load_biased_summary() -> str:
    try:
        from chloe.state.kv import get as kv_get
        return (kv_get("reflect:biased_summary") or "").strip()
    except Exception:
        return ""


def _load_recurring_loops() -> str:
    try:
        from chloe.state.kv import get as kv_get
        loops = kv_get("reflect:recurring_loops") or []
        if not loops:
            return ""
        return "\n".join(f"- {l}" for l in loops[:3])
    except Exception:
        return ""


async def _load_narrative_block(message: str, n: int = 3) -> str:
    """Semantic query over narrative_entries — the primary world model source once entries accumulate."""
    try:
        from chloe.memory.narrative_store import query
        entries = query(message, n=n)
        if not entries:
            return ""
        return "\n\n".join(entries)
    except Exception:
        return ""


def _load_anticipations_block(limit: int = 2) -> str:
    """Return high-intensity unresolved anticipations."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """SELECT text, valence, intensity FROM inner_anticipations
               WHERE resolved=0 AND intensity >= 0.5
               ORDER BY intensity DESC, created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        if not rows:
            return ""
        lines = []
        for r in rows:
            v = float(r["valence"])
            qualifier = "dreading" if v < -0.3 else ("looking forward to" if v > 0.3 else "holding")
            lines.append(f"- {qualifier}: {r['text']}")
        return "\n".join(lines)
    except Exception:
        return ""


def _load_teo_affect_block(person_id: str, max_age_hours: int = 24) -> str:
    """Return a brief note on Teo's apparent emotional state from the last exchange."""
    try:
        from chloe.state.db import get_connection
        from chloe.state.kv import get as kv_get
        conn = get_connection()
        pid = int(person_id)
        row = conn.execute(
            """SELECT valence, arousal, engagement_quality, created_at FROM person_affect_log
               WHERE person_id=? AND created_at >= datetime('now', ?)
               ORDER BY created_at DESC LIMIT 1""",
            (pid, f"-{max_age_hours} hours"),
        ).fetchone()
        if not row:
            return ""
        v = float(row["valence"])
        a = float(row["arousal"])
        eq = float(row["engagement_quality"] if "engagement_quality" in row.keys() else 0.5)
        if abs(v) < 0.2 and abs(a - 0.4) < 0.15 and abs(eq - 0.5) < 0.25:
            return ""  # neutral — not worth noting
        if v < -0.4:
            mood = "low or flat"
        elif v > 0.4:
            mood = "warm or up"
        else:
            mood = "somewhat mixed"
        energy = "quieter than usual" if a < 0.25 else ("energised" if a > 0.7 else "")
        parts = [mood]
        if energy:
            parts.append(energy)
        base = "Teo seemed " + ", ".join(parts) + " in the last exchange"
        if eq < 0.3:
            base += " — and a bit elsewhere, like he was checking in from a distance"
        elif eq > 0.75:
            base += " — and fully here"
        return base + "."
    except Exception:
        return ""


def _load_rupture_block() -> str:
    try:
        from chloe.affect.arc import active_rupture
        rupture = active_rupture()
        if not rupture:
            return ""
        note = rupture.get("note", "").strip()
        intensity = rupture.get("intensity", 0.5)
        if note:
            return f"{note} (intensity {intensity:.2f})"
        return f"There's a relational rupture active (intensity {intensity:.2f}). Be careful and tender."
    except Exception:
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
    """Return a note about how long it's been since the last conversation, if > threshold.

    Incorporates the qualitative register of how the last session ended — not just
    how long the gap was, but what kind of gap it was.
    """
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
            base = f"It's been about {int(hours)} hours since your last exchange."
        else:
            days = int(hours / 24)
            base = f"It's been about {days} day{'s' if days != 1 else ''} since your last exchange."

        # Enrich with the qualitative register of how the last session ended
        register_note = _last_session_register_note()
        if register_note:
            return f"{base} {register_note}"
        return f"{base} You've been thinking in the gap."
    except Exception:
        return ""


def _last_session_register_note() -> str:
    """Return a qualitative note about how the last session ended, if available."""
    try:
        from chloe.state.kv import get as kv_get
        reg = kv_get("chat:last_session_register") or {}
        if not reg:
            return ""
        valence = float(reg.get("person_valence", 0.0))
        ambiguity = float(reg.get("ambiguity", 0.2))
        parts = []
        if ambiguity > 0.55:
            parts.append("the last thing you talked about wasn't quite finished")
        if valence < -0.35:
            parts.append("it ended on something heavy")
        elif valence > 0.4:
            parts.append("it ended warmly")
        if parts:
            return "Note: " + "; ".join(parts) + "."
        return ""
    except Exception:
        return ""


def _load_world_beliefs(limit: int = 5) -> str:
    """Return top world beliefs by confidence for injection into the chat prompt.

    Phrasing reflects developmental stage of the belief:
      - confidence < 0.4              → "something that might be true"
      - confidence 0.4 ≤ x ≤ 0.65     → "something you've started to think"
      - confidence > 0.65             → "something you believe"
    Noticings (proto-beliefs, not yet beliefs) are surfaced with even softer
    language. Ambivalent pairs (contradictions held without resolution) are
    named as such — don't hide the tension.
    Capped at `limit` rows regardless of total count.
    """
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """SELECT id, topic, belief, confidence, noticing, ambivalent, ambivalent_with
               FROM world_beliefs
               ORDER BY confidence DESC, updated_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        if not rows:
            return ""
        groups: dict[str, list[str]] = {
            "ambivalent": [],
            "noticings": [],
            "low": [],
            "mid": [],
            "high": [],
        }
        seen_ambivalent_pairs: set[tuple] = set()
        for r in rows:
            conf = float(r["confidence"] or 0.0)
            line = f"- {r['topic']}: {r['belief']}"
            if r["ambivalent"] and r["ambivalent_with"]:
                pair_key = tuple(sorted([r["id"], r["ambivalent_with"]]))
                if pair_key in seen_ambivalent_pairs:
                    continue
                seen_ambivalent_pairs.add(pair_key)
                # Try to find the other belief text for full pairing
                other = conn.execute(
                    "SELECT topic, belief FROM world_beliefs WHERE id=?", (r["ambivalent_with"],)
                ).fetchone()
                if other:
                    line = f"- {r['topic']}: {r['belief']} / but also: {other['belief']}"
                groups["ambivalent"].append(line)
            elif r["noticing"]:
                groups["noticings"].append(line)
            elif conf < 0.4:
                groups["low"].append(line)
            elif conf <= 0.65:
                groups["mid"].append(line)
            else:
                groups["high"].append(line)

        sections: list[str] = []
        if groups["ambivalent"]:
            sections.append(
                "Things you hold in two directions at once — both feel true, neither wins:\n"
                + "\n".join(groups["ambivalent"])
            )
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


def _load_inner_questions_block(limit: int = 2) -> str:
    """Return top active open questions — things Chloe is genuinely wondering about."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT text, domain FROM inner_questions WHERE resolved=0 ORDER BY intensity DESC LIMIT ?",
            (limit,),
        ).fetchall()
        if not rows:
            return ""
        return "\n".join(f"- {r['text']}" for r in rows)
    except Exception:
        return ""


def _load_teo_read_block() -> str:
    """Return the synthesized standing read on Teo if available."""
    try:
        from chloe.state.kv import get as kv_get
        return (kv_get("identity:teo_read") or "").strip()
    except Exception:
        return ""


def _load_aesthetic_orientation() -> str:
    """Return the aesthetic orientation block — what she's drawn toward, not reactive."""
    try:
        from chloe.state.kv import get as kv_get
        return (kv_get("identity:aesthetic_orientation") or "").strip()
    except Exception:
        return ""


def _load_novelty_deficit_block() -> str:
    """Return a background texture note when novelty_deficit is high."""
    try:
        from chloe.state.kv import get as kv_get
        deficit = float(kv_get("affect:novelty_deficit") or 0.0)
        if deficit < 0.55:
            return ""
        return "Looking for something to catch — recent inputs have been flat, nothing has surprised you."
    except Exception:
        return ""


def _onboarding_note() -> str:
    """Return an onboarding block for early conversations before context accumulates.

    Fires when there are fewer than 15 memories (not counting system/action sources),
    signaling that this is genuinely early. Suppresses it after the first bootstrap.
    """
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE source NOT IN ('action', 'system') AND archived_tier='hot'"
        ).fetchone()[0]
        if count >= 15:
            return ""
        return (
            "## This is early\n"
            "You and Teo are still at the beginning. You don't know him well yet — "
            "don't perform depth you haven't earned. Be curious. Ask one thing if something "
            "catches. Let the relationship develop at its own pace."
        )
    except Exception:
        return ""


_SUFFIX_TOKEN_BUDGET = 2500  # ≈10k chars; Flash context is large but recency bias worsens past here


def _apply_token_budget(parts: list[str], max_tokens: int = _SUFFIX_TOKEN_BUDGET) -> str:
    """Join parts, dropping from the back until total is within budget.

    Voice anchor (always last) and onboarding (always first, if present) are
    protected. Blocks between them are dropped tail-first, which removes the
    lower-priority context (teo-read, aesthetic, novelty) before the higher-
    priority blocks (affect, memories, inner state).
    """
    if not parts:
        return ""
    anchor = parts[-1]
    body = parts[:-1]
    budget_chars = max_tokens * 4
    while body and len("\n\n".join(body)) > budget_chars:
        body.pop()
    return "\n\n".join(body + [anchor])


def _voice_anchor() -> str:
    """Short voice reminder appended after all context blocks.

    Placed last so recency bias in long system prompts reinforces the voice
    rules rather than the meta-cognitive blocks that precede it.
    """
    return (
        "## Stay yourself\n"
        "Short sentences. Say the thing, then explain it — not the other way around. "
        "If a tool returns success, say what happened in one line, in your voice — not the API's voice. "
        "Don't start with 'I'. Don't summarize before you speak."
    )
