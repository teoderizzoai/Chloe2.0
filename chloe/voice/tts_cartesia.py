from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import AsyncIterable

from chloe.observability.logging import get_logger

log = get_logger("voice.tts.cartesia")

_CARTESIA_URL = "https://api.cartesia.ai/tts/bytes"
_CHUNK_SIZE = 4096


async def synthesize_stream(
    text_iter: AsyncIterable[str],
    *,
    api_key: str | None = None,
    voice_id: str | None = None,
) -> AsyncIterator[bytes]:
    """Synthesize text tokens into raw PCM audio chunks via Cartesia.

    Accumulates all text tokens, then POSTs to Cartesia `/tts/bytes` and
    streams the response body back as 16-bit PCM at 16 kHz.
    """
    _api_key = api_key or os.environ.get("CARTESIA_API_KEY", "")
    _voice_id = voice_id or os.environ.get("CARTESIA_VOICE_ID", "")

    if not _api_key:
        log.warning("cartesia_no_api_key")
        return

    parts: list[str] = []
    async for token in text_iter:
        parts.append(token)

    full_text = "".join(parts).strip()
    if not full_text:
        return

    try:
        import httpx

        headers = {
            "X-API-Key": _api_key,
            "Cartesia-Version": "2024-06-10",
            "Content-Type": "application/json",
        }
        payload = {
            "model_id": "sonic-english",
            "transcript": full_text,
            "voice": {"mode": "id", "id": _voice_id},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": 16000,
            },
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", _CARTESIA_URL, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        yield chunk
    except Exception as exc:
        log.warning("cartesia_failed", error=str(exc))
