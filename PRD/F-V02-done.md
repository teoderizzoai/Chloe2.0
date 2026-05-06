# F-V02 · `voice/tts_cartesia.py` — Cartesia streaming TTS

## Overview

Implement `synthesize_stream(text_iter) -> AsyncIterator[bytes]`. Calls Cartesia streaming API with the cloned voice ID from config. Emits audio chunks in PCM format. Provide a fallback adapter `tts_elevenlabs.py` following the same interface.

## Configuration

```python
# Add to config.py Settings:
cartesia_api_key: str = ""        # Cartesia API key
cartesia_voice_id: str = ""       # Cloned voice ID
cartesia_model_id: str = "sonic-english"
elevenlabs_api_key: str = ""      # ElevenLabs fallback API key
elevenlabs_voice_id: str = ""     # ElevenLabs voice ID
tts_backend: str = "cartesia"     # "cartesia" or "elevenlabs"
tts_sample_rate: int = 24000      # Output sample rate Hz
```

## Implementation — Cartesia

```python
# chloe/voice/tts_cartesia.py

import json
import asyncio
from typing import AsyncIterator
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("voice.tts.cartesia")


async def synthesize_stream(text_iter: AsyncIterator[str]) -> AsyncIterator[bytes]:
    """
    Stream TTS from Cartesia API.
    Yields audio chunks as bytes (PCM format).
    """
    settings = get_settings()
    if not settings.cartesia_api_key:
        log.error("cartesia_api_key_missing")
        return

    # Collect text tokens into reasonable chunks (sentence boundaries)
    async for audio_chunk in _stream_cartesia(text_iter, settings):
        yield audio_chunk


async def _stream_cartesia(
    text_iter: AsyncIterator[str],
    settings,
) -> AsyncIterator[bytes]:
    """Connect to Cartesia WebSocket streaming API."""
    import websockets

    ws_url = "wss://api.cartesia.ai/tts/websocket"
    headers = {
        "X-API-Key": settings.cartesia_api_key,
        "Cartesia-Version": "2024-06-10",
    }

    try:
        async with websockets.connect(ws_url, extra_headers=headers) as ws:
            # Send chunks as they arrive from the text stream
            async def send_text():
                buffer = ""
                async for token in text_iter:
                    buffer += token
                    # Send at sentence boundaries for better prosody
                    if any(c in buffer for c in ".!?\n") or len(buffer) > 100:
                        request = {
                            "model_id": settings.cartesia_model_id,
                            "transcript": buffer,
                            "voice": {
                                "mode": "id",
                                "id": settings.cartesia_voice_id,
                            },
                            "output_format": {
                                "container": "raw",
                                "encoding": "pcm_f32le",
                                "sample_rate": settings.tts_sample_rate,
                            },
                            "context_id": "chloe_voice",
                            "continue": True,
                        }
                        await ws.send(json.dumps(request))
                        buffer = ""
                # Send any remaining text
                if buffer.strip():
                    request = {
                        "model_id": settings.cartesia_model_id,
                        "transcript": buffer,
                        "voice": {"mode": "id", "id": settings.cartesia_voice_id},
                        "output_format": {
                            "container": "raw",
                            "encoding": "pcm_f32le",
                            "sample_rate": settings.tts_sample_rate,
                        },
                        "context_id": "chloe_voice",
                        "continue": False,  # Final chunk
                    }
                    await ws.send(json.dumps(request))

            send_task = asyncio.create_task(send_text())

            try:
                async for msg in ws:
                    if isinstance(msg, bytes):
                        yield msg
                    elif isinstance(msg, str):
                        data = json.loads(msg)
                        if data.get("done"):
                            break
                        if data.get("type") == "chunk":
                            audio_b64 = data.get("data", "")
                            if audio_b64:
                                import base64
                                yield base64.b64decode(audio_b64)
            finally:
                send_task.cancel()

    except Exception as exc:
        log.warning("cartesia_error", error=str(exc))
```

## Implementation — ElevenLabs fallback

```python
# chloe/voice/tts_elevenlabs.py

import json
from typing import AsyncIterator
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("voice.tts.elevenlabs")


async def synthesize_stream(text_iter: AsyncIterator[str]) -> AsyncIterator[bytes]:
    """ElevenLabs streaming TTS fallback."""
    settings = get_settings()
    if not settings.elevenlabs_api_key:
        log.error("elevenlabs_api_key_missing")
        return

    import httpx

    # Collect all text first (ElevenLabs streaming requires full text upfront for low latency)
    full_text = ""
    async for token in text_iter:
        full_text += token

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{settings.elevenlabs_voice_id}/stream"
    payload = {
        "text": full_text,
        "model_id": "eleven_turbo_v2_5",
        "output_format": "pcm_24000",
    }
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    log.warning("elevenlabs_error", status=resp.status_code)
                    return
                async for chunk in resp.aiter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk
    except httpx.RequestError as exc:
        log.warning("elevenlabs_network_error", error=str(exc))
```

## Dispatcher

```python
# chloe/voice/tts.py

from typing import AsyncIterator
from chloe.config import get_settings


async def synthesize_stream(text_iter: AsyncIterator[str]) -> AsyncIterator[bytes]:
    """Dispatch to configured TTS backend."""
    settings = get_settings()
    if settings.tts_backend == "elevenlabs":
        from chloe.voice.tts_elevenlabs import synthesize_stream as el_stream
        async for chunk in el_stream(text_iter):
            yield chunk
    else:
        from chloe.voice.tts_cartesia import synthesize_stream as cartesia_stream
        async for chunk in cartesia_stream(text_iter):
            yield chunk
```

## Dependencies

- `websockets` — WebSocket client for Cartesia.
- `httpx` — HTTP client for ElevenLabs.
- F-02 (`config.py` — TTS settings).

## Testing

### Unit tests — `tests/unit/test_tts_cartesia.py`

```python
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


async def _text_iter(*tokens):
    for t in tokens:
        yield t


@pytest.mark.asyncio
async def test_synthesize_stream_yields_audio_chunks(monkeypatch):
    """Mocked Cartesia WS → receives audio bytes."""
    settings = MagicMock(
        cartesia_api_key="test_key",
        cartesia_voice_id="voice_1",
        cartesia_model_id="sonic-english",
        tts_sample_rate=24000,
    )
    monkeypatch.setattr("chloe.voice.tts_cartesia.get_settings", lambda: settings)

    fake_audio = b"\x00\x01\x02\x03" * 100

    class MockWS:
        def __init__(self):
            self._messages = [fake_audio, fake_audio]
            self._idx = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._idx >= len(self._messages):
                raise StopAsyncIteration
            msg = self._messages[self._idx]
            self._idx += 1
            return msg

        async def send(self, data):
            pass

    with patch("websockets.connect", return_value=MockWS()):
        from chloe.voice.tts_cartesia import synthesize_stream
        chunks = []
        async for chunk in synthesize_stream(_text_iter("Hello there.", "How are you?")):
            chunks.append(chunk)

    assert len(chunks) > 0
    assert all(isinstance(c, bytes) for c in chunks)


@pytest.mark.asyncio
async def test_no_api_key_returns_empty(monkeypatch):
    """Missing API key → no chunks, no exception."""
    monkeypatch.setattr("chloe.voice.tts_cartesia.get_settings",
                        lambda: MagicMock(cartesia_api_key=""))

    from chloe.voice.tts_cartesia import synthesize_stream
    chunks = []
    async for chunk in synthesize_stream(_text_iter("test")):
        chunks.append(chunk)
    assert chunks == []


@pytest.mark.asyncio
async def test_elevenlabs_fallback_streams(monkeypatch):
    """ElevenLabs fallback yields audio bytes."""
    monkeypatch.setattr("chloe.voice.tts_elevenlabs.get_settings", lambda: MagicMock(
        elevenlabs_api_key="el_key",
        elevenlabs_voice_id="voice_2",
    ))

    fake_chunks = [b"audio_chunk_1", b"audio_chunk_2"]

    class MockStreamResponse:
        status_code = 200
        async def aiter_bytes(self, chunk_size=4096):
            for c in fake_chunks:
                yield c
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    class MockClient:
        def stream(self, *a, **kw):
            return MockStreamResponse()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass

    with patch("httpx.AsyncClient", return_value=MockClient()):
        from chloe.voice.tts_elevenlabs import synthesize_stream
        chunks = []
        async for chunk in synthesize_stream(_text_iter("Hello")):
            chunks.append(chunk)

    assert b"audio_chunk_1" in chunks
```

## Acceptance criteria

- Mocked Cartesia WebSocket → `synthesize_stream` yields bytes (PCM audio).
- Mocked ElevenLabs API → `tts_elevenlabs.synthesize_stream` yields bytes.
- Missing API key → empty generator, no exception, warning logged.
- Dispatcher routes to correct backend based on `tts_backend` config.
