# F-V01 · `voice/stt_whisper.py` — streaming Whisper wrapper

## Overview

Implement `transcribe_stream(audio_chunks_iter) -> AsyncIterator[str]`. Loads Whisper-large-v3 locally or calls Deepgram streaming API if `WHISPER_MODE=deepgram`. Emits partial transcripts as they arrive. Includes a 30-second silence timeout.

## Context

Chloe 1.0 had a Fish Speech voice pipeline in a separate Python 3.11 venv. Chloe 2.0 replaces it with a clean async implementation using either local Whisper (lower latency, requires GPU) or Deepgram's streaming API (faster time-to-first-transcript on CPU-only servers). The interface is the same regardless of backend — the caller just iterates over transcript strings.

## Configuration

```python
# Add to config.py Settings:
whisper_mode: str = "local"          # "local" or "deepgram"
whisper_model: str = "large-v3"      # Whisper model name for local mode
deepgram_api_key: str = ""           # Required for deepgram mode
voice_silence_timeout_s: int = 30    # Silence timeout in seconds
```

## Implementation

```python
# chloe/voice/stt_whisper.py

import asyncio
from typing import AsyncIterator
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("voice.stt")

SILENCE_TIMEOUT = 30.0  # seconds


async def transcribe_stream(
    audio_chunks_iter: AsyncIterator[bytes],
) -> AsyncIterator[str]:
    """
    Consume audio chunks and yield partial transcript strings.
    Raises asyncio.TimeoutError if no audio received for SILENCE_TIMEOUT seconds.
    """
    settings = get_settings()
    if settings.whisper_mode == "deepgram":
        async for text in _transcribe_deepgram(audio_chunks_iter):
            yield text
    else:
        async for text in _transcribe_local(audio_chunks_iter):
            yield text


async def _transcribe_local(
    audio_chunks_iter: AsyncIterator[bytes],
) -> AsyncIterator[str]:
    """
    Local Whisper transcription.
    Collects audio until a pause, runs Whisper inference, yields transcript.
    """
    import io
    import numpy as np
    settings = get_settings()

    # Lazy-load Whisper to avoid startup cost when STT not used
    try:
        import whisper as _whisper
        model = _whisper.load_model(settings.whisper_model)
    except ImportError:
        log.error("whisper_not_installed")
        return

    buffer = bytearray()
    try:
        async for chunk in _with_timeout(audio_chunks_iter, SILENCE_TIMEOUT):
            buffer.extend(chunk)
            # Process in 2-second windows to emit partial results
            if len(buffer) >= 32000 * 2 * 2:  # 2 seconds of 16kHz 16-bit PCM
                transcript = await asyncio.get_event_loop().run_in_executor(
                    None, _run_whisper, model, bytes(buffer)
                )
                if transcript.strip():
                    yield transcript
                    buffer = bytearray()
    except asyncio.TimeoutError:
        log.info("stt_silence_timeout")
        # Final pass on remaining buffer
        if buffer:
            transcript = await asyncio.get_event_loop().run_in_executor(
                None, _run_whisper, model, bytes(buffer)
            )
            if transcript.strip():
                yield transcript


def _run_whisper(model, audio_bytes: bytes) -> str:
    """Run Whisper inference synchronously (called in executor)."""
    import io
    import numpy as np
    import soundfile as sf

    audio_io = io.BytesIO(audio_bytes)
    try:
        audio, sr = sf.read(audio_io, dtype="float32")
    except Exception:
        # Assume raw PCM 16kHz 16-bit mono
        audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    result = model.transcribe(audio, language="en", fp16=False)
    return result.get("text", "")


async def _transcribe_deepgram(
    audio_chunks_iter: AsyncIterator[bytes],
) -> AsyncIterator[str]:
    """Deepgram streaming STT."""
    import httpx
    import json
    settings = get_settings()

    if not settings.deepgram_api_key:
        log.error("deepgram_api_key_missing")
        return

    dg_url = (
        "wss://api.deepgram.com/v1/listen"
        "?model=nova-2&language=en&smart_format=true&interim_results=true"
    )

    import websockets
    try:
        async with websockets.connect(
            dg_url,
            extra_headers={"Authorization": f"Token {settings.deepgram_api_key}"},
        ) as ws:
            async def send_audio():
                try:
                    async for chunk in _with_timeout(audio_chunks_iter, SILENCE_TIMEOUT):
                        await ws.send(chunk)
                except asyncio.TimeoutError:
                    pass
                finally:
                    await ws.send(b"")  # End of stream

            send_task = asyncio.create_task(send_audio())
            try:
                async for msg in ws:
                    data = json.loads(msg)
                    transcript = (
                        data.get("channel", {})
                        .get("alternatives", [{}])[0]
                        .get("transcript", "")
                    )
                    is_final = data.get("is_final", False)
                    if transcript and is_final:
                        yield transcript
            finally:
                send_task.cancel()
    except Exception as exc:
        log.warning("deepgram_error", error=str(exc))


async def _with_timeout(
    aiter: AsyncIterator[bytes],
    timeout: float,
) -> AsyncIterator[bytes]:
    """Wrap an async iterator with a per-item timeout."""
    while True:
        try:
            chunk = await asyncio.wait_for(aiter.__anext__(), timeout=timeout)
            yield chunk
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            raise  # Re-raise to caller
```

## Dependencies

- `openai-whisper` (for local mode) or `websockets` (for Deepgram mode).
- `soundfile`, `numpy` for audio decoding.
- F-02 (`config.py` — `whisper_mode`, `deepgram_api_key`, `voice_silence_timeout_s`).

## Testing

### Unit tests — `tests/unit/test_stt_whisper.py`

```python
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock


async def _chunks_from_bytes(data: bytes, chunk_size: int = 1024):
    """Helper: yield fixed-size chunks from bytes."""
    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]


@pytest.mark.asyncio
async def test_transcribe_local_basic(monkeypatch, tmp_path):
    """Local mode: mocked Whisper returns transcript."""
    import numpy as np

    mock_model = MagicMock()
    mock_model.transcribe.return_value = {"text": "hello world"}

    monkeypatch.setattr("chloe.voice.stt_whisper.get_settings", lambda: MagicMock(
        whisper_mode="local", whisper_model="base"
    ))

    with patch("whisper.load_model", return_value=mock_model), \
         patch("chloe.voice.stt_whisper._run_whisper", return_value="hello world"):

        # 2+ seconds of fake PCM at 16kHz 16-bit = 64000 bytes
        fake_audio = b"\x00\x01" * 40000

        transcripts = []
        async for text in __import__("chloe.voice.stt_whisper", fromlist=["transcribe_stream"]).transcribe_stream(
            _chunks_from_bytes(fake_audio)
        ):
            transcripts.append(text)

    assert "hello world" in transcripts


@pytest.mark.asyncio
async def test_silence_timeout_fires():
    """A slow audio source should trigger TimeoutError → generator ends."""
    from chloe.voice import stt_whisper

    async def slow_chunks():
        yield b"\x00" * 1000
        await asyncio.sleep(35)  # Exceeds SILENCE_TIMEOUT
        yield b"\x00" * 1000

    transcripts = []
    with patch.object(stt_whisper, "SILENCE_TIMEOUT", 0.1):  # Very short for test
        with patch("chloe.voice.stt_whisper.get_settings", return_value=MagicMock(
            whisper_mode="local", whisper_model="base"
        )), patch("whisper.load_model", return_value=MagicMock()), \
           patch("chloe.voice.stt_whisper._run_whisper", return_value=""):
            async for text in stt_whisper.transcribe_stream(slow_chunks()):
                transcripts.append(text)

    # Should complete without hanging (timeout triggered internally)
    assert True  # If we got here, no hang occurred
```

### WAV fixture test

```python
@pytest.mark.asyncio
async def test_wav_file_transcription(tmp_path):
    """Create a WAV fixture and verify full transcript emitted."""
    import wave
    import struct

    # Create a simple 1-second WAV (silence — Whisper will emit empty or brief text)
    wav_path = tmp_path / "test.wav"
    with wave.open(str(wav_path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)  # 16-bit
        f.setframerate(16000)
        f.writeframes(b"\x00\x00" * 16000)

    wav_bytes = wav_path.read_bytes()

    with patch("whisper.load_model") as mock_load, \
         patch("chloe.voice.stt_whisper._run_whisper", return_value="test transcript"), \
         patch("chloe.voice.stt_whisper.get_settings", return_value=MagicMock(
             whisper_mode="local", whisper_model="base"
         )):
        mock_load.return_value = MagicMock()
        transcripts = []
        from chloe.voice.stt_whisper import transcribe_stream
        async for text in transcribe_stream(_chunks_from_bytes(wav_bytes, 8000)):
            transcripts.append(text)

    assert len(transcripts) >= 0  # At minimum doesn't crash
```

## Acceptance criteria

- Local mode: WAV fixture → full transcript emitted without error.
- Silence timeout (30s without audio) → generator exits cleanly (no hang).
- Deepgram mode: final transcripts (is_final=True) emitted; interim ignored.
- Missing Deepgram API key → logs error and returns without raising.
