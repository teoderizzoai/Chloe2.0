from __future__ import annotations

import asyncio
import io
import os
from collections.abc import AsyncIterator
from typing import AsyncIterable

from chloe.observability.logging import get_logger

log = get_logger("voice.stt")

SILENCE_TIMEOUT_S = 30.0
_whisper_model = None


async def transcribe_stream(
    audio_chunks_iter: AsyncIterable[bytes],
    *,
    silence_timeout: float = SILENCE_TIMEOUT_S,
) -> AsyncIterator[str]:
    """Transcribe an async stream of audio chunks, yielding transcript strings.

    Collects audio until the iterator is exhausted or `silence_timeout` seconds
    pass with no new data. Dispatches to Deepgram REST or local Whisper based
    on the WHISPER_MODE env var.
    """
    mode = os.environ.get("WHISPER_MODE", "local").lower()
    if mode == "deepgram":
        async for transcript in _deepgram_stream(audio_chunks_iter, silence_timeout):
            yield transcript
    else:
        async for transcript in _whisper_local_stream(audio_chunks_iter, silence_timeout):
            yield transcript


async def _collect_audio(
    audio_chunks_iter: AsyncIterable[bytes],
    silence_timeout: float,
) -> bytes:
    """Drain the chunk iterator with a wall-clock silence timeout."""
    buf = bytearray()
    try:
        async with asyncio.timeout(silence_timeout):
            async for chunk in audio_chunks_iter:
                buf.extend(chunk)
    except TimeoutError:
        log.info("stt_silence_timeout", bytes_so_far=len(buf))
    return bytes(buf)


async def _deepgram_stream(
    audio_chunks_iter: AsyncIterable[bytes],
    silence_timeout: float,
) -> AsyncIterator[str]:
    api_key = os.environ.get("DEEPGRAM_API_KEY", "")
    if not api_key:
        log.warning("deepgram_no_api_key")
        return

    audio = await _collect_audio(audio_chunks_iter, silence_timeout)
    if not audio:
        return

    try:
        import httpx

        url = (
            "https://api.deepgram.com/v1/listen"
            "?model=nova-2&encoding=linear16&sample_rate=16000"
        )
        headers = {
            "Authorization": f"Token {api_key}",
            "Content-Type": "audio/wav",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, content=audio, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        for channel in data.get("results", {}).get("channels", []):
            for alt in channel.get("alternatives", []):
                text = alt.get("transcript", "").strip()
                if text:
                    yield text
    except Exception as exc:
        log.warning("deepgram_failed", error=str(exc))


async def _whisper_local_stream(
    audio_chunks_iter: AsyncIterable[bytes],
    silence_timeout: float,
) -> AsyncIterator[str]:
    audio = await _collect_audio(audio_chunks_iter, silence_timeout)
    if not audio:
        return

    loop = asyncio.get_event_loop()
    transcript = await loop.run_in_executor(None, _run_whisper, audio)
    if transcript:
        yield transcript


def _run_whisper(audio_bytes: bytes) -> str:
    try:
        import whisper
        import numpy as np

        model = _get_whisper_model()
        try:
            import soundfile as sf

            audio_np, _ = sf.read(io.BytesIO(audio_bytes), dtype="float32")
        except Exception:
            audio_np = (
                np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            )
        result = model.transcribe(audio_np)
        return result.get("text", "").strip()
    except ImportError:
        log.warning("whisper_not_installed")
        return ""
    except Exception as exc:
        log.warning("whisper_failed", error=str(exc))
        return ""


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisper

        model_name = os.environ.get("WHISPER_MODEL", "large-v3")
        _whisper_model = whisper.load_model(model_name)
    return _whisper_model
