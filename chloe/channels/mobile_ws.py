from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chloe.observability.logging import get_logger

log = get_logger("channels.mobile_ws")

_CHAR_PREFIX_PATH = Path(__file__).parent.parent / "llm" / "prompts" / "character_prefix.md"
_MAX_HISTORY = 40  # turns (user + model pairs)
_MAX_TOOL_HOPS = 4  # Cap function-call rounds per user message


async def handle_mobile_ws(websocket: Any, person_id: str = "1") -> None:
    await websocket.accept()

    # Normalize person_id once at the boundary so all downstream code sees a
    # clean integer string and never silently falls back to person 1.
    try:
        person_id = str(int(person_id))
    except (ValueError, TypeError):
        log.warning("invalid_person_id_defaulting", raw=person_id)
        person_id = "1"

    log.info("mobile_ws_connected", person_id=person_id)

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        await websocket.send_text(json.dumps({"type": "chunk", "text": "No API key configured."}))
        await websocket.send_text(json.dumps({"type": "done", "artifact_preview": None}))
        return

    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=api_key)
    history: list[dict] = _load_history_from_db(person_id)

    # Send history to client so the UI can render prior messages on connect
    if history:
        ui_msgs = []
        for h in history:
            role = h.get("role", "")
            text = (h.get("parts") or [{}])[0].get("text", "")
            ui_msgs.append({"from": "chloe" if role == "model" else "me", "text": text})
        await websocket.send_text(json.dumps({"type": "history", "messages": ui_msgs}))

    # Per-session preflight slot cache — avoids re-resolving the same person
    # or belief slot on consecutive turns. Cleared automatically when the WS closes.
    import uuid as _uuid
    _session_id = str(_uuid.uuid4())
    _slot_cache: dict[str, str] = {}

    turns_this_session = 0
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "detail": "invalid json"}))
                continue

            msg_type = msg.get("type")

            # Handle 👍/👎 reactions for active learning
            if msg_type == "reaction":
                import asyncio
                asyncio.create_task(_handle_reaction(msg, person_id))
                continue

            if msg_type != "message":
                continue

            user_text = msg.get("text", "").strip()
            if not user_text:
                continue

            from chloe.observability import live_buffer
            preview = user_text if len(user_text) <= 160 else user_text[:157] + "…"
            live_buffer.record_event("user_message", preview, person_id=person_id)

            _persist_chat_turn("user", user_text, person_id)

            # Goodbye gate — skip LLM for clear farewells; optionally emit a terse reply.
            if _is_goodbye(user_text):
                import random
                _save_conversation_end_register()
                if random.random() < 0.30:
                    short_reply = random.choice(_GOODBYE_SHORT_REPLIES)
                    await websocket.send_text(json.dumps({"type": "chunk", "text": short_reply}))
                    _persist_chat_turn("assistant", short_reply, person_id)
                await websocket.send_text(json.dumps({"type": "done", "artifact_preview": None}))
                continue

            # Resolve any pending kinetic-sensitive confirm ticket if user consented.
            await _maybe_resolve_pending_confirm(user_text, person_id)

            # Run preflight and dynamic suffix in parallel — both start immediately.
            # Preflight (~200–350ms) routes to targeted data sources, captures facts,
            # detects verb gaps. Dynamic suffix (~50–150ms) assembles ambient context.
            # Net wait = max(preflight, suffix) not sum.
            import asyncio as _asyncio
            from chloe.channels.preflight import run_preflight
            from chloe.channels.chat_api import build_dynamic_suffix

            preflight_result, dynamic_suffix = await _asyncio.gather(
                run_preflight(user_text, history, person_id,
                              slot_cache=_slot_cache, session_id=_session_id),
                build_dynamic_suffix(person_id, user_text, salience=0.5),
            )

            # Re-assemble with salience gate applied now that preflight.salience is known
            dynamic_suffix = _trim_by_salience(dynamic_suffix, preflight_result.salience)

            reply = await _chat_reply_streaming(
                client, genai_types, user_text, person_id, history, websocket,
                preflight_context=preflight_result.context_block,
                dynamic_suffix=dynamic_suffix,
            )

            history.append({"role": "user", "parts": [{"text": user_text}]})
            history.append({"role": "model", "parts": [{"text": reply}]})
            turns_this_session += 1

            if len(history) > _MAX_HISTORY * 2:
                history = history[-(_MAX_HISTORY * 2):]

            if reply:
                _persist_chat_turn("assistant", reply, person_id)
                import asyncio
                asyncio.create_task(_extract_and_process_mentions(user_text, reply, person_id))
                asyncio.create_task(_witness_pass(user_text, reply, person_id))

            await websocket.send_text(json.dumps({"type": "done", "artifact_preview": None}))

    except Exception as exc:
        log.info("mobile_ws_closed", reason=str(exc))
        if turns_this_session >= 2:
            import asyncio
            _save_conversation_end_register()
            asyncio.create_task(_post_chat_reflect())


async def _route_kinetic_sensitive_via_gate(
    tool_name: str, verb: str, args: dict, person_id: str
) -> dict:
    """Submit a kinetic-sensitive verb through the action gate and return a
    lightweight status dict so the chat model can tell Teo confirmation is pending.
    """
    from chloe.actions.schema import Action
    from chloe.actions import gate
    from chloe.tools.registry import get_registry

    registry = get_registry()
    verb_def = registry.get_verb(tool_name, verb)
    description = verb_def.description_for_human if verb_def else f"{tool_name}.{verb}"
    pid = int(person_id)
    action = Action(
        tool=tool_name,
        verb=verb,
        args=args,
        intent=f"user requested {description} via chat",
        preview=f"{description} — requested in conversation",
        authorization="kinetic-sensitive",
        origin="chat",
        origin_person_id=pid,
    )
    result = await gate.submit(action)
    return {
        "awaiting": bool(result.awaiting),
        "suppressed": bool(result.suppressed),
        "ticket_id": result.ticket_id,
        "reason": result.reason,
    }


async def _chat_reply_streaming(
    client, genai_types, text: str, person_id: str, history: list[dict], websocket,
    preflight_context: str = "",
    dynamic_suffix: str = "",
) -> str:
    """Like _chat_reply but streams the final text turn over the websocket.

    Tool hops (structured JSON) are still sent in a single round-trip — there's
    nothing useful to stream there. Only the final text generation is chunked.
    The fully accumulated reply is returned so the caller can persist it.

    `dynamic_suffix` is pre-built by the caller (run in parallel with preflight).
    If empty, it is built here as a fallback.
    """
    import time
    _turn_start = time.monotonic()
    try:
        from chloe.tools.registry import get_registry

        if not dynamic_suffix:
            from chloe.channels.chat_api import build_dynamic_suffix
            dynamic_suffix = await build_dynamic_suffix(person_id, text)

        char_prefix = _CHAR_PREFIX_PATH.read_text() if _CHAR_PREFIX_PATH.exists() else ""
        # Preflight context goes between the character prefix and the ambient dynamic suffix
        # so the targeted info is prominent but the character voice comes first.
        if preflight_context:
            system_prompt = f"{char_prefix}\n\n{preflight_context}\n\n{dynamic_suffix}"
        else:
            system_prompt = f"{char_prefix}\n\n{dynamic_suffix}"

        # Cache for debug tab
        try:
            from chloe.state.kv import set as _kv_set
            from datetime import datetime as _dt, timezone as _tz
            _kv_set("debug:last_system_prompt", system_prompt)
            _kv_set("debug:last_dynamic_suffix", dynamic_suffix)
            _kv_set("debug:last_preflight_context", preflight_context or "")
            _kv_set("debug:last_turn_at", _dt.now(_tz.utc).isoformat())
        except Exception:
            pass

        registry = get_registry()
        tool_decls = registry.gemini_tool_declarations()
        config = genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=tool_decls,
        )

        contents = list(history) + [{"role": "user", "parts": [{"text": text}]}]

        # Tool-hop rounds (non-streaming — structured responses)
        for hop in range(_MAX_TOOL_HOPS):
            resp = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=config,
            )
            calls = _extract_function_calls(resp)
            if not calls:
                # Final text — stream it (counts as TTFT for tool-hop path)
                final_text = (resp.text or "").strip()
                if final_text:
                    ttft = time.monotonic() - _turn_start
                    log.info("chat_ttft", ttft_seconds=round(ttft, 3), path="single_shot",
                             hops=hop, system_prompt_chars=len(system_prompt))
                    await websocket.send_text(json.dumps({"type": "chunk", "text": final_text}))
                return final_text

            model_parts = [{"function_call": {"name": c["name"], "args": c["args"]}} for c in calls]
            contents.append({"role": "model", "parts": model_parts})

            response_parts = []
            for call in calls:
                fn_name = call["name"]
                args = call["args"] or {}
                tool_name, _, verb = fn_name.partition("__")
                log.info("mobile_chat_tool_call", tool=tool_name, verb=verb, hop=hop, args=args)
                try:
                    verb_def = registry.get_verb(tool_name, verb)
                    if verb_def and verb_def.auth_class == "kinetic-sensitive":
                        gate_result = await _route_kinetic_sensitive_via_gate(
                            tool_name, verb, args, person_id
                        )
                        if gate_result.get("awaiting"):
                            payload = {
                                "success": True,
                                "data": {
                                    "confirmation_required": True,
                                    "ticket_id": gate_result.get("ticket_id"),
                                    "message": (
                                        "I've queued that — say 'yes' or 'go ahead' to confirm, "
                                        "or I'll cancel it if you change your mind."
                                    ),
                                },
                            }
                        else:
                            payload = {
                                "success": False,
                                "error": gate_result.get("reason", "Could not route action through gate"),
                            }
                    else:
                        result = await registry.execute(tool_name, verb, args)
                        payload = {
                            "success": bool(result.success),
                            "data": result.data,
                            "error": result.error,
                        }
                        log.info("mobile_chat_tool_result", tool=tool_name, verb=verb,
                                 success=bool(result.success), error=result.error)
                except Exception as exc:
                    payload = {"success": False, "error": str(exc)}
                    log.warning("mobile_chat_tool_failed", tool=tool_name, verb=verb, error=str(exc))
                response_parts.append({
                    "function_response": {"name": fn_name, "response": payload},
                })
            contents.append({"role": "user", "parts": response_parts})

        # Hop cap reached — stream the final answer with no tools.
        no_tool_config = genai_types.GenerateContentConfig(system_instruction=system_prompt)
        accumulated = ""
        _ttft_logged = False
        try:
            async for chunk in await client.aio.models.generate_content_stream(
                model="gemini-2.5-flash",
                contents=contents,
                config=no_tool_config,
            ):
                chunk_text = chunk.text or ""
                if chunk_text:
                    if not _ttft_logged:
                        ttft = time.monotonic() - _turn_start
                        log.info("chat_ttft", ttft_seconds=round(ttft, 3), path="streaming",
                                 hops=_MAX_TOOL_HOPS, system_prompt_chars=len(system_prompt))
                        _ttft_logged = True
                    accumulated += chunk_text
                    await websocket.send_text(json.dumps({"type": "chunk", "text": chunk_text}))
        except Exception:
            # Fall back to single-shot if streaming not available
            final = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=no_tool_config,
            )
            accumulated = (final.text or "").strip()
            if accumulated:
                ttft = time.monotonic() - _turn_start
                log.info("chat_ttft", ttft_seconds=round(ttft, 3), path="streaming_fallback",
                         hops=_MAX_TOOL_HOPS, system_prompt_chars=len(system_prompt))
                await websocket.send_text(json.dumps({"type": "chunk", "text": accumulated}))
        total_latency = time.monotonic() - _turn_start
        log.info("chat_turn_latency", total_seconds=round(total_latency, 3))
        return accumulated.strip()
    except Exception as exc:
        log.warning("mobile_chat_streaming_failed", error=str(exc))
        try:
            await websocket.send_text(json.dumps({
                "type": "chunk",
                "text": "I'm having trouble right now — could you try again?",
            }))
        except Exception:
            pass
        return ""


def _extract_function_calls(resp) -> list[dict]:
    calls = []
    try:
        for cand in getattr(resp, "candidates", []) or []:
            content = getattr(cand, "content", None)
            for part in getattr(content, "parts", []) or []:
                fc = getattr(part, "function_call", None)
                if fc and getattr(fc, "name", None):
                    args = getattr(fc, "args", {}) or {}
                    if hasattr(args, "items"):
                        args = dict(args)
                    calls.append({"name": fc.name, "args": args})
    except Exception:
        pass
    return calls


async def _extract_and_process_mentions(user_text: str, reply: str, person_id: str) -> None:
    """Background task: extract mentions, aesthetic reactions, and tag unprocessed memories."""
    try:
        from chloe.llm.gemini import GeminiClient
        from chloe.llm.schemas import ExtractOutput
        from chloe.persons.social_graph import process_social_mentions
        from chloe.identity.aesthetics import log_reaction
        from chloe.memory.store import consider_unprocessed, mark_unprocessed

        client = GeminiClient()
        exchange = f"[user]: {user_text[:300]}\n[chloe]: {reply[:300]}"
        result = await client.flash("extract_mentions.md", {"exchange": exchange}, ExtractOutput)

        if not result:
            return

        data = result if isinstance(result, dict) else result.model_dump()
        pid = int(person_id)

        mentions = data.get("social_mentions", [])
        if mentions:
            process_social_mentions(mentions, mentioned_by_id=pid)
            log.info("mentions_processed", count=len(mentions))

        for rxn in data.get("aesthetic_reactions", []):
            try:
                rxn_d = rxn if isinstance(rxn, dict) else rxn.model_dump()
                log_reaction(
                    stimulus=rxn_d.get("stimulus", "")[:400],
                    domain=rxn_d.get("domain", "unknown"),
                    valence=float(rxn_d.get("valence", 0.0)),
                    intensity=float(rxn_d.get("intensity", 0.5)),
                    notes=rxn_d.get("notes", ""),
                    confidentiality=rxn_d.get("confidentiality", "public"),
                )
            except Exception as exc:
                log.warning("aesthetic_reaction_log_failed", error=str(exc))

        salience = float(data.get("salience", 0.3))
        ambiguity = float(data.get("ambiguity", 0.2))
        person_valence = float(data.get("person_valence", 0.0))
        person_arousal = float(data.get("person_arousal", 0.4))

        # Back-fill emotional valence/arousal on the chat memories just written.
        # The memory is written before extraction runs, so we UPDATE it here now
        # that we have the emotional register. This activates mood-congruent retrieval.
        try:
            from chloe.state.db import get_connection as _gc
            from chloe.memory.store import update_memory_affect
            _conn = _gc()
            recent_chat_ids = _conn.execute(
                "SELECT id FROM memories WHERE source='chat' AND source_ref=? "
                "ORDER BY id DESC LIMIT 2",
                (f"chat:{person_id}",),
            ).fetchall()
            for row in recent_chat_ids:
                update_memory_affect(row["id"], person_valence, person_arousal)
            if recent_chat_ids:
                log.info("chat_memory_affect_written",
                         ids=[r["id"] for r in recent_chat_ids],
                         valence=round(person_valence, 2), arousal=round(person_arousal, 2))
        except Exception as exc:
            log.warning("chat_affect_update_failed", error=str(exc))

        if consider_unprocessed(salience, ambiguity):
            from chloe.state.db import get_connection
            conn = get_connection()
            row = conn.execute(
                "SELECT id FROM memories WHERE source='chat' AND source_ref=? "
                "ORDER BY id DESC LIMIT 1",
                (f"chat:{person_id}",),
            ).fetchone()
            if row:
                mark_unprocessed(row["id"], True)
                log.info("chat_memory_marked_unprocessed", id=row["id"],
                         salience=salience, ambiguity=ambiguity)
        engagement_quality = _estimate_engagement_quality(user_text, reply)
        try:
            from chloe.state.db import get_connection
            conn = get_connection()
            conn.execute(
                "INSERT INTO person_affect_log (person_id, valence, arousal, engagement_quality, trigger) VALUES (?, ?, ?, ?, ?)",
                (int(person_id), person_valence, person_arousal, engagement_quality, user_text[:200]),
            )
            conn.commit()
        except Exception as exc:
            log.warning("person_affect_log_failed", error=str(exc))

        # Save rolling exchange register for qualitative time gap note
        try:
            from chloe.state.kv import set as kv_set
            kv_set("chat:last_exchange_register", {
                "person_valence": person_valence,
                "ambiguity": float(data.get("ambiguity", 0.2)),
                "engagement_quality": engagement_quality,
            })
        except Exception:
            pass

        # Accumulate depletion from emotionally intensive exchanges
        try:
            from chloe.affect.dims import load as load_affect, save as save_affect
            state = load_affect()
            intensity_contribution = abs(person_arousal - 0.4) * 0.1 + float(data.get("salience", 0.3)) * 0.04
            state.depletion = min(1.0, state.depletion + intensity_contribution)
            save_affect(state)
        except Exception:
            pass

        # Resolve open questions when Teo's message appears to answer them.
        # Uses word-overlap heuristic — no extra LLM call needed.
        try:
            _resolve_questions_from_turn(user_text, person_id)
        except Exception as exc:
            log.warning("question_resolution_failed", error=str(exc))

    except Exception as exc:
        log.warning("mention_extraction_failed", error=str(exc))


_Q_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or", "is",
    "are", "was", "be", "been", "being", "it", "its", "that", "this", "with",
    "by", "from", "as", "what", "how", "which", "his", "her", "their", "he",
    "she", "they", "you", "your", "i", "me", "my", "we", "our", "do", "did",
    "does", "has", "have", "had", "s",
}


def _resolve_questions_from_turn(user_text: str, person_id: str) -> None:
    """Mark inner_questions resolved when Teo's message appears to answer them.

    Heuristic: tokenise both the question and user_text, remove stopwords,
    require ≥3 overlapping content words and overlap ≥ 40% of the question's
    keywords. Domain='teo' questions only — 'self'/'world' questions need
    richer context to resolve.
    """
    from chloe.state.db import get_connection
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, text FROM inner_questions WHERE resolved=0 AND domain='teo'"
    ).fetchall()
    if not rows:
        return

    user_words = {w for w in user_text.lower().split() if w not in _Q_STOPWORDS and len(w) > 2}
    if not user_words:
        return

    resolved_ids = []
    for row in rows:
        q_words = {w for w in row["text"].lower().split() if w not in _Q_STOPWORDS and len(w) > 2}
        if not q_words:
            continue
        overlap = len(q_words & user_words)
        if overlap >= 3 and overlap / len(q_words) >= 0.4:
            resolved_ids.append(row["id"])

    if resolved_ids:
        conn.executemany(
            "UPDATE inner_questions SET resolved=1 WHERE id=?",
            [(rid,) for rid in resolved_ids],
        )
        conn.commit()
        log.info("questions_resolved_from_chat", ids=resolved_ids, person_id=person_id)


def _worth_witnessing(exchange: str, salience: float, ambiguity: float) -> bool:
    if len(exchange) < 150:
        return False
    if salience < 0.3 and ambiguity < 0.3:
        return False
    return True


async def _witness_pass(user_text: str, reply: str, person_id: str) -> None:
    """Background task: write a prose observation about this exchange if it's worth noticing."""
    try:
        from chloe.llm.gemini import GeminiClient
        from chloe.llm.schemas import WitnessOutput
        from chloe.memory.narrative_store import add_entry, query

        exchange = f"[Teo]: {user_text[:500]}\n[Chloe]: {reply[:500]}"

        # Salience gate — skip short or low-signal exchanges
        salience = 0.4  # default; reuse value from extract_and_process if possible
        ambiguity = 0.3
        if not _worth_witnessing(exchange, salience, ambiguity):
            return

        # Semantic deduplication — skip if a very similar observation exists recently
        similar = query(exchange, n=1)
        if similar and _text_similarity(similar[0], exchange) > 0.7:
            log.debug("witness_skipped_duplicate")
            return

        client = GeminiClient()
        result = await client.flash("witness.md", {"exchange": exchange}, WitnessOutput)
        if not result:
            return

        observation = (result.get("observation") if isinstance(result, dict)
                       else getattr(result, "observation", "")) or ""
        observation = observation.strip()
        if not observation:
            return

        entry_id = add_entry(observation, source="witness", salience=0.6)
        log.info("witness_entry_written", entry_id=entry_id, chars=len(observation))
    except Exception as exc:
        log.warning("witness_pass_failed", error=str(exc))


def _text_similarity(a: str, b: str) -> float:
    """Quick Jaccard similarity on word sets for dedup check."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


async def _maybe_resolve_pending_confirm(user_text: str, person_id: str) -> None:
    """If there's a pending kinetic-sensitive confirm ticket for this person and
    the user's message looks like consent, auto-resolve it.

    Consent phrases are intentionally narrow — we only fire on clear yes signals,
    not vague sentences that might contain "ok" in a different context.
    """
    _CONSENT_PHRASES = {"yes", "yeah", "yep", "ok", "okay", "sure", "do it", "go ahead",
                        "go for it", "confirm", "approve", "yes please", "sounds good"}
    normalised = user_text.strip().lower().rstrip("!.,")
    if normalised not in _CONSENT_PHRASES:
        return
    try:
        from chloe.state.db import get_connection
        from chloe.actions.confirm import confirm as confirm_ticket
        pid = int(person_id)
        conn = get_connection()
        row = conn.execute(
            """SELECT id, ticket_id, tool, verb, preview FROM chat_pending_confirms
               WHERE person_id=? AND state='pending'
               ORDER BY created_at DESC LIMIT 1""",
            (pid,),
        ).fetchone()
        if not row:
            return
        ok = await confirm_ticket(row["ticket_id"])
        state = "resolved" if ok else "expired"
        conn.execute(
            "UPDATE chat_pending_confirms SET state=?, resolved_at=datetime('now') WHERE id=?",
            (state, row["id"]),
        )
        conn.commit()
        log.info("chat_confirm_resolved", ticket_id=row["ticket_id"], ok=ok,
                 tool=row["tool"], verb=row["verb"])
    except Exception as exc:
        log.warning("chat_confirm_resolution_failed", error=str(exc))



def _estimate_engagement_quality(user_text: str, chloe_reply: str) -> float:
    """Heuristic: estimate how present/engaged Teo seems from his message.

    Returns 0.0–1.0. Short messages with no referential pickup score low;
    longer messages that pick up prior thread score high.
    """
    words = user_text.split()
    word_count = len(words)

    score = 0.5  # baseline
    if word_count < 5:
        score -= 0.25
    elif word_count > 40:
        score += 0.2
    elif word_count > 15:
        score += 0.1

    # Bonus when message references content from Chloe's prior reply
    if chloe_reply:
        reply_words = set(chloe_reply.lower().split())
        user_words = set(w.lower() for w in words)
        overlap = len(user_words & reply_words) / max(len(user_words), 1)
        if overlap > 0.15:
            score += 0.2

    # Signs of engagement: questions, exclamations, named things
    if "?" in user_text:
        score += 0.05
    if "!" in user_text:
        score += 0.05

    return max(0.0, min(1.0, score))


def _save_conversation_end_register() -> None:
    """Copy the last exchange register to last_session_register when a conversation closes."""
    try:
        from chloe.state.kv import get as kv_get, set as kv_set
        reg = kv_get("chat:last_exchange_register") or {}
        if reg:
            kv_set("chat:last_session_register", reg)
    except Exception:
        pass


async def _post_chat_reflect() -> None:
    """Trigger a reflect pass immediately after a chat session ends.

    Runs regardless of the 2h cooldown so that Chloe can process what was
    just said before the next reflect window. The LAST_REFLECT_KEY is reset
    so the scheduled 2h timer doesn't double-fire too soon.
    """
    try:
        from chloe.reflect.every_2h import run_reflect
        result = await run_reflect(force=True)
        log.info("post_chat_reflect_done", applied=result.get("applied") if result else None)
    except Exception as exc:
        log.warning("post_chat_reflect_failed", error=str(exc))


async def _handle_reaction(msg: dict, person_id: str) -> None:
    """Store a 👍/👎 reaction for active learning in weekly procedural distillation.

    Expected message shape: {type: "reaction", reaction: "thumbs_up"|"thumbs_down", reply_id: int|null}
    """
    try:
        reaction = msg.get("reaction", "")
        if reaction not in ("thumbs_up", "thumbs_down"):
            return
        reply_id = msg.get("reply_id")
        pid = int(person_id)

        from chloe.state.db import get_connection
        conn = get_connection()

        # If no reply_id given, attach to the most recent assistant memory
        if not reply_id:
            row = conn.execute(
                "SELECT id FROM memories WHERE source='chat' AND source_ref=? "
                "AND text LIKE 'I said:%' ORDER BY id DESC LIMIT 1",
                (f"chat:{person_id}",),
            ).fetchone()
            reply_id = row["id"] if row else None

        conn.execute(
            "INSERT INTO reply_reactions (reply_memory_id, person_id, reaction) VALUES (?, ?, ?)",
            (reply_id, pid, reaction),
        )
        conn.commit()
        log.info("reply_reaction_stored", reaction=reaction, reply_id=reply_id, person_id=person_id)
    except Exception as exc:
        log.warning("handle_reaction_failed", error=str(exc))


_GOODBYE_PHRASES = {
    "bye", "goodbye", "ciao", "see you", "see ya", "good night", "goodnight",
    "talk later", "talk soon", "ttyl", "gn", "night", "later", "gotta go",
    "heading out", "heading off", "going to bed", "going to sleep",
    "talk tomorrow", "until tomorrow", "adios", "peace", "take care",
}

_GOODBYE_SHORT_REPLIES = [
    "night.", "later.", "ok.", "yeah.", "good.", "got it.", "see you.",
]


def _is_goodbye(text: str) -> bool:
    """True when the message is clearly a farewell with no substantial content."""
    lower = text.strip().lower().rstrip("!.,~")
    if lower in _GOODBYE_PHRASES:
        return True
    for phrase in _GOODBYE_PHRASES:
        if lower.startswith(phrase) and len(lower) < len(phrase) + 12:
            return True
    return False


def _person_name(person_id: str) -> str:
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        row = conn.execute("SELECT name FROM persons WHERE id=?", (int(person_id),)).fetchone()
        return row["name"] if row else "them"
    except Exception:
        return "them"


def _persist_chat_turn(role: str, text: str, person_id: str) -> None:
    """Write a chat turn as a low-salience episodic memory."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        snippet = text if len(text) <= 1000 else text[:1000] + "…"
        name = _person_name(person_id) if role == "user" else None
        prefix = f"{name} said" if name else "I said"
        body = f"{prefix}: {snippet}"
        conn.execute(
            """
            INSERT INTO memories (kind, text, source, source_ref, tags, salience, weight, created_at)
            VALUES ('episodic', ?, 'chat', ?, '["chat"]', 0.3, 0.6, ?)
            """,
            (body, f"chat:{person_id}", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except Exception as exc:
        log.warning("chat_persist_failed", error=str(exc))


def _load_history_from_db(person_id: str, limit: int = _MAX_HISTORY * 2) -> list[dict]:
    """Restore recent chat turns from memories table for Gemini context."""
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        rows = conn.execute(
            """SELECT text FROM memories
               WHERE source='chat' AND source_ref=?
               ORDER BY created_at ASC""",
            (f"chat:{person_id}",),
        ).fetchall()
        # Keep only the most recent `limit` turns
        rows = rows[-limit:]
        history = []
        for row in rows:
            text = row["text"] or ""
            if text.startswith("I said: "):
                history.append({"role": "model", "parts": [{"text": text[8:]}]})
            else:
                # "{Name} said: {text}" — strip the prefix
                colon = text.find(": ")
                body = text[colon + 2:] if colon != -1 else text
                history.append({"role": "user", "parts": [{"text": body}]})
        return history
    except Exception as exc:
        log.warning("history_load_failed", error=str(exc))
        return []


# Heavy introspective blocks skipped for routine (low-salience) messages.
# These sections are expensive in system-prompt tokens and irrelevant when
# Teo is asking about the weather or sharing a quick thought.
_ROUTINE_SKIP_HEADERS = {
    "## Patterns you keep falling into",
    "## Background texture",
    "## Things you're genuinely wondering about",
    "## How your current state is shaping your perception",
    "## What to recalibrate this week",
    "## How you read him",
    "## What you're drawn toward",
    "## How Teo seemed recently",
}


def _trim_by_salience(dynamic: str, salience: float) -> str:
    """Remove heavy introspective blocks when the message is routine (salience < 0.4)."""
    if salience >= 0.4:
        return dynamic
    # The suffix is assembled as "\n\n".join(parts) where each part starts with "## Header\n..."
    parts = dynamic.split("\n\n")
    kept = []
    for part in parts:
        first_line = part.split("\n")[0].strip()
        if any(first_line.startswith(h) for h in _ROUTINE_SKIP_HEADERS):
            continue
        kept.append(part)
    return "\n\n".join(kept)
