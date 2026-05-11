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
    history: list[dict] = []

    turns_this_session = 0
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "detail": "invalid json"}))
                continue

            if msg.get("type") != "message":
                continue

            user_text = msg.get("text", "").strip()
            if not user_text:
                continue

            from chloe.observability import live_buffer
            preview = user_text if len(user_text) <= 160 else user_text[:157] + "…"
            live_buffer.record_event("user_message", preview, person_id=person_id)

            _persist_chat_turn("user", user_text, person_id)

            # Resolve any pending kinetic-sensitive confirm ticket if user consented.
            await _maybe_resolve_pending_confirm(user_text, person_id)

            reply = await _chat_reply_streaming(
                client, genai_types, user_text, person_id, history, websocket
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
                asyncio.create_task(_run_intercept(user_text, person_id))

            await websocket.send_text(json.dumps({"type": "done", "artifact_preview": None}))

    except Exception as exc:
        log.info("mobile_ws_closed", reason=str(exc))
        if turns_this_session >= 2:
            import asyncio
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
    client, genai_types, text: str, person_id: str, history: list[dict], websocket
) -> str:
    """Like _chat_reply but streams the final text turn over the websocket.

    Tool hops (structured JSON) are still sent in a single round-trip — there's
    nothing useful to stream there. Only the final text generation is chunked.
    The fully accumulated reply is returned so the caller can persist it.
    """
    try:
        from chloe.channels.chat_api import build_dynamic_suffix
        from chloe.tools.registry import get_registry

        dynamic = await build_dynamic_suffix(person_id, text)
        char_prefix = _CHAR_PREFIX_PATH.read_text() if _CHAR_PREFIX_PATH.exists() else ""
        system_prompt = f"{char_prefix}\n\n{dynamic}"

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
                # Final text — stream it
                final_text = (resp.text or "").strip()
                if final_text:
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
        try:
            async for chunk in await client.aio.models.generate_content_stream(
                model="gemini-2.5-flash",
                contents=contents,
                config=no_tool_config,
            ):
                chunk_text = chunk.text or ""
                if chunk_text:
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
                await websocket.send_text(json.dumps({"type": "chunk", "text": accumulated}))
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
                log_reaction(
                    stimulus=rxn.get("stimulus", "")[:400],
                    domain=rxn.get("domain", "unknown"),
                    valence=float(rxn.get("valence", 0.0)),
                    intensity=float(rxn.get("intensity", 0.5)),
                    notes=rxn.get("notes", ""),
                )
            except Exception as exc:
                log.warning("aesthetic_reaction_log_failed", error=str(exc))

        salience = float(data.get("salience", 0.3))
        ambiguity = float(data.get("ambiguity", 0.2))
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
    except Exception as exc:
        log.warning("mention_extraction_failed", error=str(exc))


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


async def _run_intercept(user_text: str, person_id: str) -> None:
    """Background wrapper for the message intercept layer. Never raises."""
    try:
        from chloe.channels.intercept import run_intercept
        await run_intercept(user_text, person_id)
    except Exception as exc:
        log.warning("intercept_task_failed", error=str(exc))


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


def _persist_chat_turn(role: str, text: str, person_id: str) -> None:
    """Write a chat turn as a low-salience episodic memory.

    Salience is intentionally low (0.3) so chat fragments don't dominate
    retrieval, but they remain available for grading and for nightly
    consolidation to extract semantic facts from.
    """
    try:
        from chloe.state.db import get_connection
        conn = get_connection()
        snippet = text if len(text) <= 1000 else text[:1000] + "…"
        prefix = "Teo said" if role == "user" else "I said"
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
