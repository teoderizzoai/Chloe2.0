from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import AsyncIterable

from chloe.observability.logging import get_logger

log = get_logger("voice.tts.elevenlabs")

_CHUNK_SIZE = 4096


async def synthesize_stream(
    text_iter: AsyncIterable[str],
    *,
    api_key: str | None = None,
    voice_id: str | None = None,
) -> AsyncIterator[bytes]:
    """Synthesize text tokens into PCM audio chunks via ElevenLabs.

    Follows the same interface as tts_cartesia.synthesize_stream so callers
    can swap providers by changing the import.
    """
    _api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
    _voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "")

    if not _api_key:
        log.warning("elevenlabs_no_api_key")
        return

    parts: list[str] = []
    async for token in text_iter:
        parts.append(token)

    full_text = "".join(parts).strip()
    if not full_text:
        return

    try:
        import httpx

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{_voice_id}/stream"
        headers = {
            "xi-api-key": _api_key,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        }
        payload = {
            "text": full_text,
            "model_id": "eleven_turbo_v2",
            "output_format": "pcm_16000",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        yield chunk
    except Exception as exc:
        log.warning("elevenlabs_failed", error=str(exc))
