from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any

from chloe.observability.logging import get_logger

log = get_logger("voice.realtime")

VOICE_REPLY_MAX_TOKENS = 200


async def handle_voice_session(websocket: Any) -> None:
    """Full realtime voice pipeline over a FastAPI WebSocket.

    Flow per utterance:
      1. Receive opus/PCM audio chunks from the client until silence or interrupt.
      2. Run STT (Whisper local or Deepgram) on the buffered audio.
      3. Send the transcript to the LLM (capped at VOICE_REPLY_MAX_TOKENS).
      4. Stream TTS (Cartesia) audio chunks back over the WebSocket.

    Interrupt: the client sends {"type":"interrupt"} as a JSON text frame at any
    point; all in-flight STT/LLM/TTS tasks are cancelled atomically.
    """
    interrupt_event = asyncio.Event()

    async def _recv_audio() -> AsyncIterator[bytes]:
        while not interrupt_event.is_set():
            try:
                msg = await asyncio.wait_for(websocket.receive(), timeout=31.0)
            except (asyncio.TimeoutError, Exception):
                break

            if msg.get("type") == "websocket.disconnect":
                break

            if "bytes" in msg and msg["bytes"]:
                yield msg["bytes"]
            elif "text" in msg and msg["text"]:
                try:
                    parsed = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if parsed.get("type") == "interrupt":
                    log.info("voice_interrupt_received")
                    interrupt_event.set()
                    break

    async def _pipeline() -> None:
        from chloe.voice.stt_whisper import transcribe_stream
        from chloe.voice.tts_cartesia import synthesize_stream

        transcript_parts: list[str] = []
        async for partial in transcribe_stream(_recv_audio()):
            transcript_parts.append(partial)

        if interrupt_event.is_set() or not transcript_parts:
            return

        transcript = " ".join(transcript_parts).strip()
        if not transcript:
            return

        log.info("voice_transcript", chars=len(transcript))

        reply_text = await _get_reply(transcript)
        if interrupt_event.is_set() or not reply_text:
            return

        log.info("voice_reply_ready", chars=len(reply_text))

        async def _text_tokens() -> AsyncIterator[str]:
            yield reply_text

        async for audio_chunk in synthesize_stream(_text_tokens()):
            if interrupt_event.is_set():
                break
            await websocket.send_bytes(audio_chunk)

        if not interrupt_event.is_set():
            await websocket.send_text(json.dumps({"type": "done"}))

    try:
        await _pipeline()
    except asyncio.CancelledError:
        log.info("voice_session_cancelled")
    except Exception as exc:
        log.warning("voice_session_error", error=str(exc))


async def _get_reply(transcript: str) -> str:
    """Call Gemini with the voice transcript, capping output at VOICE_REPLY_MAX_TOKENS."""
    try:
        from google import genai
        from google.genai import types as genai_types
        from pathlib import Path

        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            return ""

        char_prefix_path = Path(__file__).parent.parent / "llm" / "prompts" / "character_prefix.md"
        system_prompt = char_prefix_path.read_text() if char_prefix_path.exists() else ""
        system_prompt += "\n\n[VOICE MODE: reply in 1-3 short sentences only.]"

        client = genai.Client(api_key=api_key)
        resp = await client.aio.models.generate_content(
            model="gemini-2.5-flash",
            contents=transcript,
            config=genai_types.GenerateContentConfig(
                system_instruction=system_prompt,
                max_output_tokens=VOICE_REPLY_MAX_TOKENS,
            ),
        )
        return resp.text.strip() if resp.text else ""
    except Exception as exc:
        log.warning("voice_reply_failed", error=str(exc))
        return ""
