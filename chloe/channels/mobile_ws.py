from __future__ import annotations

import json
from typing import Any

from chloe.observability.logging import get_logger

log = get_logger("channels.mobile_ws")

_MAX_HISTORY = 100


async def handle_mobile_ws(websocket: Any, person_id: str = "1") -> None:
    """Mobile chat WebSocket handler at /v1/mobile/ws.

    Protocol:
      client → server: JSON text frame  {"type":"message","text":"…"}
      server → client: JSON text frame  {"type":"chunk","text":"…"}
      server → client: JSON text frame  {"type":"done","artifact_preview":…}

    The server keeps no local message cache; caching is the client's responsibility
    (F-M02 spec: last 100 messages in Expo SQLite).
    """
    await websocket.accept()
    log.info("mobile_ws_connected", person_id=person_id)

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

            reply = await _chat_reply(user_text, person_id)
            await websocket.send_text(json.dumps({"type": "chunk", "text": reply}))
            await websocket.send_text(json.dumps({"type": "done", "artifact_preview": None}))

    except Exception as exc:
        log.info("mobile_ws_closed", reason=str(exc))


async def _chat_reply(text: str, person_id: str) -> str:
    try:
        import os
        from google import genai
        from google.genai import types as genai_types
        from chloe.channels.chat_api import build_dynamic_suffix
        from pathlib import Path

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return "I'm here."

        dynamic = await build_dynamic_suffix(person_id, text)
        char_prefix_path = Path(__file__).parent.parent / "llm" / "prompts" / "character_prefix.md"
        system_prompt = char_prefix_path.read_text() if char_prefix_path.exists() else ""
        system_prompt += f"\n\n{dynamic}"

        client = genai.Client(api_key=api_key)
        resp = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=text,
            config=genai_types.GenerateContentConfig(system_instruction=system_prompt),
        )
        return resp.text.strip() if resp.text else ""
    except Exception as exc:
        log.warning("mobile_chat_failed", error=str(exc))
        return ""
