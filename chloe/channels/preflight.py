"""Pre-generation preflight analysis.

Runs before Chloe replies. One Flash call with conversation history that
does three jobs simultaneously:

  1. Context routing  — which data sources to fetch (person records, inbox,
                        calendar, inner state, targeted memories)
  2. Task detection   — is something being asked of Chloe? which verb?
                        verb gaps are queued as proposals for later.
  3. Memory capture   — facts/events worth storing before the reply is generated
                        so they're available in the next turn.

The resolved context is injected at the top of the system prompt, above the
standard dynamic suffix. The whole thing runs in parallel with
build_dynamic_suffix() — net added latency is roughly the difference between
the Flash call (~200–350ms) and the baseline assembly (~50–150ms).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from chloe.observability.logging import get_logger

log = get_logger("channels.preflight")

_NETWORK_TIMEOUT = 0.6   # seconds for inbox / calendar slots
_MAX_HISTORY_TURNS = 6   # user+model pairs to include in the prompt
_SESSION_SUMMARY_TURN_THRESHOLD = 12  # turns before prepending session summary
_SESSION_SUMMARY_REGEN_INTERVAL = 10  # regen summary every N turns

# Module-level tool catalog cache — rebuilt only when load_dynamic_verbs() fires.
_tool_catalog_cache: str | None = None


def invalidate_tool_catalog_cache() -> None:
    """Call this after load_dynamic_verbs() so the next preflight rebuilds."""
    global _tool_catalog_cache
    _tool_catalog_cache = None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_preflight(
    user_text: str,
    history: list[dict],
    person_id: str,
    slot_cache: dict[str, str] | None = None,
    session_id: str | None = None,
) -> "PreflightResult":
    """Analyse the message and resolve context slots.

    Returns a PreflightResult whose .context_block is ready to prepend to
    the system prompt, and whose .captures have already been written to memory.
    Always returns a valid result even on failure.

    Args:
        slot_cache: Per-session dict {source_key → resolved_text}. If provided,
                    resolved slots are stored here and reused on the next turn
                    when the same source is requested. Invalidate on session close.
        session_id: Optional UUID string identifying the WebSocket session.
                    Used for kv-cached session summaries in long conversations.
    """
    try:
        from chloe.llm.gemini import GeminiClient
        from chloe.llm.schemas import PreflightOutput

        client = GeminiClient()
        if not client._api_key:
            return PreflightResult.empty()

        history_text = await _format_history_with_summary(history, session_id, client)
        tool_catalog = _get_tool_catalog()
        context = {
            "message": user_text[:800],
            "history": history_text,
            "now_iso": datetime.now(timezone.utc).isoformat(timespec="minutes"),
            "tool_catalog": tool_catalog,
        }

        output = await client.flash("preflight.md", context, PreflightOutput)
        if not output:
            return PreflightResult.empty()

        data: PreflightOutput = output if not isinstance(output, dict) else _coerce(output)

        # Generate a batch_ref for all captures from this turn
        batch_ref = str(uuid.uuid4())

        # Resolve context slots and write captures concurrently
        slot_task = _resolve_slots(data.context_slots, person_id, slot_cache, data.felt_orientation)
        capture_task = _write_captures(data.captures, person_id, batch_ref)
        verb_task = _queue_verb_proposals(data.requests, person_id, user_text)

        slot_block, _, _ = await asyncio.gather(slot_task, capture_task, verb_task)

        log.info(
            "preflight_done",
            topic=data.message_topic[:80] if data.message_topic else "",
            salience=round(data.salience, 2),
            slots=len(data.context_slots),
            captures=len(data.captures),
            requests=len(data.requests),
            has_felt_orientation=bool(data.felt_orientation),
        )

        return PreflightResult(
            context_block=slot_block,
            message_topic=data.message_topic or "",
            salience=data.salience,
            felt_orientation=data.felt_orientation,
        )

    except Exception as exc:
        log.warning("preflight_failed", error=str(exc))
        return PreflightResult.empty()


class PreflightResult:
    __slots__ = ("context_block", "message_topic", "salience", "felt_orientation")

    def __init__(
        self,
        context_block: str,
        message_topic: str,
        salience: float,
        felt_orientation: str | None = None,
    ):
        self.context_block = context_block
        self.message_topic = message_topic
        self.salience = salience
        self.felt_orientation = felt_orientation

    @classmethod
    def empty(cls) -> "PreflightResult":
        return cls(context_block="", message_topic="", salience=0.3)


# ---------------------------------------------------------------------------
# Slot resolution
# ---------------------------------------------------------------------------

async def _resolve_slots(
    slots,
    person_id: str,
    slot_cache: dict[str, str] | None = None,
    felt_orientation: str | None = None,
) -> str:
    sections: list[str] = []

    if felt_orientation and felt_orientation.strip():
        sections.append(f"## First orientation\n{felt_orientation.strip()}")

    if not slots:
        return "\n\n".join(sections)

    tasks = [_resolve_one(s, person_id, slot_cache) for s in slots]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    blocks = [r for r in results if r and not isinstance(r, Exception) and r.strip()]

    if blocks:
        sections.append("## Specifically relevant context\n\n" + "\n\n".join(blocks))

    return "\n\n".join(sections)


async def _resolve_one(slot, person_id: str, slot_cache: dict[str, str] | None = None) -> str:
    source: str = (slot.source if not isinstance(slot, dict) else slot.get("source", "")) or ""
    reason: str = (slot.reason if not isinstance(slot, dict) else slot.get("reason", "")) or ""
    label = f"*({reason})*\n" if reason else ""

    # Check session slot cache before resolving
    if slot_cache is not None and source in slot_cache:
        cached = slot_cache[source]
        if cached:
            log.debug("preflight_slot_cache_hit", source=source)
            return cached

    try:
        result = ""
        if source.startswith("person:"):
            name = source[7:].strip()
            block = _resolve_person(name)
            result = f"### About {name}\n{label}{block}" if block else ""

        elif source == "inbox":
            block = await asyncio.wait_for(_resolve_inbox(), timeout=_NETWORK_TIMEOUT)
            result = f"### Recent inbox\n{label}{block}" if block else ""

        elif source == "calendar":
            block = await asyncio.wait_for(_resolve_calendar(), timeout=_NETWORK_TIMEOUT)
            result = f"### Upcoming calendar\n{label}{block}" if block else ""

        elif source == "inner_wants":
            block = _resolve_inner_wants()
            result = f"### What Chloe is currently carrying\n{label}{block}" if block else ""

        elif source.startswith("world_beliefs:"):
            topic = source[14:].strip()
            block = _resolve_world_beliefs(topic)
            result = f"### What Chloe believes about '{topic}'\n{label}{block}" if block else ""

        elif source.startswith("memories:"):
            query = source[9:].strip()
            block = _resolve_memories(query)
            result = f"### Targeted memory recall: '{query}'\n{label}{block}" if block else ""

        # Store resolved result in session cache
        if slot_cache is not None:
            slot_cache[source] = result

        return result

    except asyncio.TimeoutError:
        log.info("preflight_slot_timeout", source=source)
        # Return a fallback note so the LLM knows it asked for something unavailable
        timeout_note = f"### {source.split(':')[0].capitalize()} (unavailable)\n*({source} slot timed out — content unavailable this turn)*"
        if slot_cache is not None:
            slot_cache[source] = ""  # don't cache timed-out slots
        return timeout_note
    except Exception as exc:
        log.warning("preflight_slot_failed", source=source, error=str(exc))

    return ""


def _resolve_person(name: str) -> str:
    if not name:
        return ""
    try:
        from chloe.state.db import get_connection
        from chloe.persons.attachment import relationship_label
        from chloe.persons.social_graph import format_person_context_for_prompt
        from chloe.identity.character_addendum import load_addendum

        conn = get_connection()
        # When the query returns multiple persons, pick the one with highest attachment_depth
        rows = conn.execute(
            """SELECT id, name, relationship_class, gen_level, attachment_depth,
                      stance, impression, trait_profile
               FROM persons
               WHERE lower(name) = lower(?) OR lower(name) LIKE lower(?)
               ORDER BY attachment_depth DESC
               LIMIT 3""",
            (name, f"%{name}%"),
        ).fetchall()

        if not rows:
            return f"No record found for '{name}'."

        if len(rows) > 1:
            matched_names = [r["name"] for r in rows]
            log.info("preflight_person_ambiguous", query=name, matches=matched_names)

        row = rows[0]  # highest attachment_depth
        pid = row["id"]
        label = relationship_label(row["attachment_depth"] or 0.0)
        lines = [
            f"Name: {row['name']}",
            f"Relationship: {label} (depth {round(row['attachment_depth'] or 0.0, 2)})",
            f"Class: {row['relationship_class'] or 'unknown'}, gen_level: {row['gen_level'] or 0}",
        ]
        if row["stance"]:
            lines.append(f"Stance: {row['stance']}")
        if row["impression"]:
            lines.append(f"Impression: {row['impression']}")
        if row["trait_profile"]:
            lines.append(f"Trait profile: {row['trait_profile']}")

        # Person context (cross-references) if gen_level >= 1
        ctx = format_person_context_for_prompt(pid)
        if ctx:
            lines.append(ctx)

        # Character addendum if gen_level >= 3
        addendum = load_addendum(pid)
        if addendum:
            lines.append(f"How Chloe is with them: {addendum}")

        return "\n".join(lines)
    except Exception as exc:
        log.warning("resolve_person_failed", name=name, error=str(exc))
        return ""


async def _resolve_inbox() -> str:
    try:
        from chloe.tools.registry import get_registry
        registry = get_registry()
        result = await registry.execute("gmail", "list_recent", {"max_results": 5})
        if result.success and result.data:
            items = result.data if isinstance(result.data, list) else result.data.get("messages", [])
            if items:
                lines = [f"- {m.get('subject','(no subject)')} from {m.get('from','?')}" for m in items[:5]]
                return "\n".join(lines)
        return ""
    except Exception:
        return ""


async def _resolve_calendar() -> str:
    try:
        from chloe.tools.registry import get_registry
        registry = get_registry()
        result = await registry.execute("calendar", "get_upcoming", {"days": 7})
        if result.success and result.data:
            items = result.data if isinstance(result.data, list) else result.data.get("events", [])
            if items:
                lines = [f"- {e.get('title','?')} at {e.get('start','?')}" for e in items[:5]]
                return "\n".join(lines)
        return ""
    except Exception:
        return ""


def _resolve_inner_wants() -> str:
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        wants = conn.execute(
            "SELECT text, pressure FROM inner_wants WHERE resolved=0 ORDER BY pressure DESC LIMIT 3"
        ).fetchall()
        fears = conn.execute(
            "SELECT text, pressure FROM inner_fears WHERE resolved=0 ORDER BY pressure DESC LIMIT 2"
        ).fetchall()
        lines = [f"- (want, p={r['pressure']:.2f}) {r['text']}" for r in wants]
        lines += [f"- (fear, p={r['pressure']:.2f}) {r['text']}" for r in fears]
        return "\n".join(lines) if lines else ""
    except Exception:
        return ""


def _resolve_world_beliefs(topic: str) -> str:
    if not topic:
        return ""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """SELECT belief, confidence, noticing FROM world_beliefs
               WHERE lower(topic) LIKE lower(?) ORDER BY confidence DESC LIMIT 3""",
            (f"%{topic}%",),
        ).fetchall()
        if not rows:
            return f"No beliefs on record for '{topic}'."
        lines = []
        for r in rows:
            conf = float(r["confidence"] or 0.0)
            qualifier = "notices" if r["noticing"] else ("believes" if conf > 0.65 else "thinks")
            lines.append(f"- Chloe {qualifier} (conf={conf:.2f}): {r['belief']}")
        return "\n".join(lines)
    except Exception:
        return ""


def _resolve_memories(query: str) -> str:
    if not query:
        return ""
    try:
        from chloe.memory.retrieval import query_fast
        memories = query_fast(query, n=4)
        if not memories:
            return ""
        return "\n".join(f"- {m.text[:200]}" for m in memories)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Memory capture
# ---------------------------------------------------------------------------

async def _write_captures(captures, person_id: str, batch_ref: str) -> None:
    if not captures:
        return
    try:
        from chloe.memory import store as mem_store
        from chloe.state.db import get_connection
        pid = int(person_id) if str(person_id).isdigit() else 1

        for cap in captures:
            text = (cap.text if not isinstance(cap, dict) else cap.get("text", "")) or ""
            if not text.strip():
                continue
            kind = (cap.kind if not isinstance(cap, dict) else cap.get("kind", "semantic")) or "semantic"
            if kind not in ("episodic", "semantic", "autobiographical", "procedural"):
                kind = "semantic"
            tags: list[str] = list(cap.tags if not isinstance(cap, dict) else cap.get("tags") or [])
            salience = float(cap.salience if not isinstance(cap, dict) else cap.get("salience", 0.5) or 0.5)
            person_name = (cap.person_name if not isinstance(cap, dict) else cap.get("person_name")) or None
            if person_name and f"person:{person_name.lower()}" not in tags:
                tags.append(f"person:{person_name.lower()}")
            tags.append("preflight_capture")

            confidentiality = (
                cap.confidentiality if not isinstance(cap, dict)
                else cap.get("confidentiality", "public")
            ) or "public"
            confidential_to = pid if confidentiality == "private" else None

            # --- 1c: Capture deduplication via Chroma cosine check ---
            if _is_duplicate_capture(text):
                log.info("preflight_capture_deduped", text_preview=text[:60])
                continue

            mem_id = mem_store.add(
                kind=kind,
                text=text,
                source="preflight",
                source_ref=f"preflight:{person_id}",
                tags=tags,
                salience=salience,
                weight=0.9,
                confidential_to=confidential_to,
            )

            # --- 4d: Write batch_ref and subject_person_id ---
            subject_person_id = _resolve_subject_person_id(person_name) if person_name else None
            conn = get_connection()
            conn.execute(
                "UPDATE memories SET batch_ref=?, subject_person_id=? WHERE id=?",
                (batch_ref, subject_person_id, mem_id),
            )

            # --- 2b: Mark older conflicting memory as superseded ---
            _maybe_supersede_older(text, tags, mem_id, conn)

            conn.commit()

            log.info("preflight_capture_written", id=mem_id, kind=kind, salience=salience,
                     confidential_to=confidential_to, batch_ref=batch_ref,
                     subject_person_id=subject_person_id, text_preview=text[:60])
    except Exception as exc:
        log.warning("preflight_capture_failed", error=str(exc))


def _is_duplicate_capture(text: str) -> bool:
    """Return True if a near-identical memory (cosine > 0.92) exists within the last 7 days."""
    try:
        from chloe.state.chroma import get_collection
        from chloe.state.db import get_connection
        collection = get_collection("memories_v2")
        if collection.count() == 0:
            return False
        resp = collection.query(
            query_texts=[text],
            n_results=1,
            include=["distances"],
        )
        ids = resp.get("ids", [[]])[0]
        distances = resp.get("distances", [[]])[0]
        if not ids or not distances:
            return False
        dist = distances[0]
        cosine = 1.0 / (1.0 + dist)
        if cosine < 0.92:
            return False
        mem_id = int(ids[0])
        conn = get_connection()
        row = conn.execute("SELECT created_at FROM memories WHERE id=?", (mem_id,)).fetchone()
        if not row:
            return False
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - created).days
        return age_days <= 7
    except Exception:
        return False


def _maybe_supersede_older(text: str, tags: list[str], new_id: int, conn) -> None:
    """If a recent semantic memory has overlapping tags and high cosine similarity,
    mark it as superseded by the new one (fact correction case)."""
    try:
        from chloe.state.chroma import get_collection
        collection = get_collection("memories_v2")
        if collection.count() == 0:
            return
        resp = collection.query(
            query_texts=[text],
            n_results=5,
            include=["distances"],
        )
        ids = resp.get("ids", [[]])[0]
        distances = resp.get("distances", [[]])[0]
        tag_set = set(tags)
        for cid, dist in zip(ids, distances):
            try:
                old_id = int(cid)
            except (TypeError, ValueError):
                continue
            if old_id == new_id:
                continue
            cosine = 1.0 / (1.0 + dist)
            if cosine < 0.82:  # lower threshold for supersede vs dedup
                continue
            row = conn.execute(
                "SELECT created_at, tags, superseded_by FROM memories WHERE id=?", (old_id,)
            ).fetchone()
            if not row or row["superseded_by"] is not None:
                continue  # already superseded
            # Check recency (within 30 days) and tag overlap
            try:
                created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - created).days > 30:
                    continue
            except Exception:
                continue
            old_tags = set(json.loads(row["tags"])) if row["tags"] else set()
            if len(tag_set & old_tags) == 0:
                continue  # no tag overlap → different topic
            conn.execute("UPDATE memories SET superseded_by=? WHERE id=?", (new_id, old_id))
            log.info("preflight_memory_superseded", old_id=old_id, new_id=new_id, cosine=round(cosine, 3))
    except Exception:
        pass


def _resolve_subject_person_id(person_name: str) -> int | None:
    """Resolve a person name to persons.id for the subject_person_id FK."""
    if not person_name:
        return None
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM persons WHERE lower(name)=lower(?) OR lower(name) LIKE lower(?) LIMIT 1",
            (person_name, f"%{person_name}%"),
        ).fetchone()
        return row["id"] if row else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Verb proposals (gap detection — queued for background processing)
# ---------------------------------------------------------------------------

async def _queue_verb_proposals(requests, person_id: str, raw_text: str) -> None:
    if not requests:
        return
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        pid = int(person_id) if str(person_id).isdigit() else 1
        for req in requests:
            verb_gap = (req.verb_gap if not isinstance(req, dict) else req.get("verb_gap", False))
            if not verb_gap:
                continue
            intent = (req.text if not isinstance(req, dict) else req.get("text", "")) or ""
            if not intent.strip():
                continue
            tool_hint = (req.suggested_tool if not isinstance(req, dict) else req.get("suggested_tool")) or None
            verb_hint = (req.suggested_verb if not isinstance(req, dict) else req.get("suggested_verb")) or None
            rationale = (req.rationale if not isinstance(req, dict) else req.get("rationale", "")) or ""

            existing = conn.execute(
                "SELECT id FROM verb_proposals WHERE status='pending' AND intent=? "
                "AND created_at >= datetime('now', '-1 day') LIMIT 1",
                (intent,),
            ).fetchone()
            if existing:
                continue

            conn.execute(
                """INSERT INTO verb_proposals
                     (requested_text, tool_hint, verb_hint, intent, rationale,
                      person_id, confidence, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
                (raw_text[:600], tool_hint, verb_hint, intent, rationale, pid, 0.6),
            )
            conn.commit()
            log.info("verb_proposal_queued_from_preflight", tool_hint=tool_hint, verb_hint=verb_hint)
    except Exception as exc:
        log.warning("preflight_verb_proposal_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _format_history_with_summary(
    history: list[dict],
    session_id: str | None,
    client,
) -> str:
    """Render last N turns as text; prepend a session summary for long sessions."""
    session_turns = len(history) // 2

    session_summary = ""
    if session_turns > _SESSION_SUMMARY_TURN_THRESHOLD and session_id:
        session_summary = await _get_or_generate_session_summary(history, session_id, session_turns, client)

    base = _format_history(history)
    if session_summary:
        return f"[Session so far: {session_summary}]\n\n{base}"
    return base


async def _get_or_generate_session_summary(
    history: list[dict],
    session_id: str,
    turn_count: int,
    client,
) -> str:
    """Return a kv-cached one-sentence session summary, regenerating every 10 turns."""
    try:
        from chloe.state.kv import get as kv_get, set as kv_set
        from chloe.llm.schemas import MessageBody

        cache_key = f"session:{session_id}:summary"
        turn_key = f"session:{session_id}:summary_turn"

        last_turn = int(kv_get(turn_key) or 0)
        cached = kv_get(cache_key)

        if cached and (turn_count - last_turn) < _SESSION_SUMMARY_REGEN_INTERVAL:
            return str(cached)

        # Summarize the earlier portion of the session (exclude last 6 turns)
        older_history = history[:-((_MAX_HISTORY_TURNS * 2))] if len(history) > _MAX_HISTORY_TURNS * 2 else history
        older_text = _format_history(older_history)
        if not older_text.strip():
            return str(cached or "")

        result = await client.flash("session_summary.md", {"history": older_text[:2000]}, MessageBody)
        if result:
            summary = (result.get("body") if isinstance(result, dict) else getattr(result, "body", "")) or ""
            summary = summary.strip()
            if summary:
                kv_set(cache_key, summary)
                kv_set(turn_key, turn_count)
                return summary

        return str(cached or "")
    except Exception:
        return ""


def _format_history(history: list[dict]) -> str:
    """Render last N turns of chat history as readable text."""
    if not history:
        return "(no prior conversation in this session)"
    turns = history[-((_MAX_HISTORY_TURNS * 2)):]
    lines = []
    for entry in turns:
        role = entry.get("role", "")
        parts = entry.get("parts", [])
        text = " ".join(p.get("text", "") for p in parts if "text" in p).strip()
        if not text:
            continue
        speaker = "Teo" if role == "user" else "Chloe"
        lines.append(f"[{speaker}]: {text[:300]}")
    return "\n".join(lines) if lines else "(no prior conversation in this session)"


def _get_tool_catalog() -> str:
    global _tool_catalog_cache
    if _tool_catalog_cache is not None:
        return _tool_catalog_cache
    try:
        from chloe.tools.registry import get_registry
        from chloe.config import FEATURE_FLAGS
        registry = get_registry()
        lines: list[str] = []
        for name, tool in registry._tools.items():
            if not FEATURE_FLAGS.get(name, True):
                continue
            verbs = list(tool.verbs.keys())
            if verbs:
                lines.append(f"- {name}: {', '.join(verbs)}")
        result = "\n".join(lines) if lines else "(no tools registered)"
        _tool_catalog_cache = result
        return result
    except Exception:
        return "(tool catalog unavailable)"


def _coerce(data: dict) -> "PreflightOutput":
    from chloe.llm.schemas import PreflightOutput
    try:
        return PreflightOutput.model_validate(data)
    except Exception:
        return PreflightOutput()
