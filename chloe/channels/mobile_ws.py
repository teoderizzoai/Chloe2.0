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

            reply = await _chat_reply(client, genai_types, user_text, person_id, history)

            history.append({"role": "user", "parts": [{"text": user_text}]})
            history.append({"role": "model", "parts": [{"text": reply}]})
            turns_this_session += 1

            if len(history) > _MAX_HISTORY * 2:
                history = history[-(_MAX_HISTORY * 2):]

            if reply:
                _persist_chat_turn("assistant", reply, person_id)

            await websocket.send_text(json.dumps({"type": "chunk", "text": reply}))
            await websocket.send_text(json.dumps({"type": "done", "artifact_preview": None}))

    except Exception as exc:
        log.info("mobile_ws_closed", reason=str(exc))
        if turns_this_session >= 2:
            import asyncio
            asyncio.create_task(_post_chat_reflect())


async def _chat_reply(client, genai_types, text: str, person_id: str, history: list[dict]) -> str:
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

        for hop in range(_MAX_TOOL_HOPS):
            resp = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=contents,
                config=config,
            )

            calls = _extract_function_calls(resp)
            if not calls:
                return (resp.text or "").strip()

            # Append the model turn that issued the calls
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
                        payload = {
                            "success": False,
                            "error": "kinetic-sensitive verbs require explicit confirmation; route via the action gate, not chat.",
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

        # Hop cap reached — ask for a final text answer with no tools.
        final = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
        )
        return (final.text or "").strip()
    except Exception as exc:
        log.warning("mobile_chat_failed", error=str(exc))
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
