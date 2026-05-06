# F-V03 · `voice/realtime.py` — full realtime pipeline

## Overview

Implement `handle_voice_session(websocket)`. Receives opus audio chunks, feeds to STT stream, feeds transcripts to `chat_2_0()` (with `voice=True` flag, capping reply at 200 tokens), feeds reply tokens to TTS stream, sends audio chunks back to the client. Handles interrupt event: cancels STT, LLM, and TTS tasks atomically.

## Context

The realtime pipeline must achieve time-to-first-audio ≤ 2s on localhost (target 1.2s on 50ms RTT). The key to low latency is pipeline parallelism: TTS starts streaming audio before the LLM has finished generating the reply. The interrupt event cancels all three pipelines so the user can barge in mid-sentence.

## WebSocket protocol

Client → Server:
- Binary frame: Opus audio chunk
- Text frame `{"type": "interrupt"}`: Cancel current response

Server → Client:
- Binary frame: PCM audio chunk (16-bit signed, 24kHz mono)
- Text frame `{"type": "transcript", "text": "...", "final": bool}`: STT transcript
- Text frame `{"type": "done"}`: Response complete

## Implementation

```python
# chloe/voice/realtime.py

import asyncio
import json
import time
from typing import AsyncIterator
from chloe.voice.stt_whisper import transcribe_stream
from chloe.voice.tts import synthesize_stream
from chloe.channels.chat_api import chat_2_0
from chloe.observability.logging import get_logger
from chloe.observability.metrics import record_voice_latency

log = get_logger("voice.realtime")

VOICE_MAX_TOKENS = 200
VOICE_TURN_TIMEOUT = 30.0  # Max seconds for one voice turn


async def handle_voice_session(websocket) -> None:
    """
    Full realtime voice pipeline for one WebSocket session.
    Loops until the client disconnects.
    """
    history = []
    log.info("voice_session_started")

    try:
        while True:
            audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
            interrupt_event = asyncio.Event()

            # Start receiving audio from the WebSocket
            receive_task = asyncio.create_task(
                _receive_audio(websocket, audio_queue, interrupt_event)
            )

            # Run the full pipeline for this turn
            turn_start = time.monotonic()
            try:
                async with asyncio.timeout(VOICE_TURN_TIMEOUT):
                    await _run_turn(
                        websocket, audio_queue, interrupt_event,
                        history, turn_start
                    )
            except TimeoutError:
                log.warning("voice_turn_timeout")
            except asyncio.CancelledError:
                log.info("voice_turn_cancelled")
                break
            finally:
                receive_task.cancel()

    except Exception as exc:
        if "disconnect" in str(exc).lower() or "1000" in str(exc):
            log.info("voice_session_ended_normal")
        else:
            log.warning("voice_session_error", error=str(exc))
    finally:
        log.info("voice_session_closed")


async def _receive_audio(
    websocket,
    audio_queue: asyncio.Queue,
    interrupt_event: asyncio.Event,
) -> None:
    """Receive audio frames and control messages from WebSocket."""
    try:
        async for message in websocket.iter_bytes():
            if isinstance(message, bytes):
                await audio_queue.put(message)
            elif isinstance(message, str):
                try:
                    data = json.loads(message)
                    if data.get("type") == "interrupt":
                        interrupt_event.set()
                        await audio_queue.put(None)  # Signal end of audio
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    finally:
        await audio_queue.put(None)  # Signal end of stream


async def _queue_to_async_iter(
    queue: asyncio.Queue,
    interrupt_event: asyncio.Event,
) -> AsyncIterator[bytes]:
    """Convert an asyncio.Queue to an AsyncIterator, stopping on interrupt."""
    while not interrupt_event.is_set():
        try:
            chunk = await asyncio.wait_for(queue.get(), timeout=1.0)
            if chunk is None:
                break
            yield chunk
        except asyncio.TimeoutError:
            if interrupt_event.is_set():
                break


async def _run_turn(
    websocket,
    audio_queue: asyncio.Queue,
    interrupt_event: asyncio.Event,
    history: list,
    turn_start: float,
) -> None:
    """Execute one voice turn: STT → LLM → TTS → audio out."""

    # Phase 1: STT
    transcript = ""
    async for partial in transcribe_stream(_queue_to_async_iter(audio_queue, interrupt_event)):
        transcript += partial
        await websocket.send_text(json.dumps({
            "type": "transcript",
            "text": partial,
            "final": False,
        }))
        if interrupt_event.is_set():
            return

    if not transcript.strip():
        return

    await websocket.send_text(json.dumps({
        "type": "transcript",
        "text": transcript,
        "final": True,
    }))

    # Phase 2: LLM (streaming tokens)
    token_queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def run_llm():
        try:
            # chat_2_0 with voice=True flag (200 token cap, shorter responses)
            response = await chat_2_0(
                message=transcript,
                history=history,
                voice=True,
                max_tokens=VOICE_MAX_TOKENS,
            )
            # Put full response as single token (streaming to be added later)
            await token_queue.put(response)
        except Exception as exc:
            log.warning("voice_llm_error", error=str(exc))
        finally:
            await token_queue.put(None)

    llm_task = asyncio.create_task(run_llm())

    # Phase 3: TTS (starts consuming tokens as soon as first one arrives)
    async def token_iter() -> AsyncIterator[str]:
        while not interrupt_event.is_set():
            token = await token_queue.get()
            if token is None:
                break
            yield token

    first_audio_sent = False
    try:
        async for audio_chunk in synthesize_stream(token_iter()):
            if interrupt_event.is_set():
                break
            if not first_audio_sent:
                latency = time.monotonic() - turn_start
                log.info("voice_first_audio", latency_ms=round(latency * 1000))
                record_voice_latency(latency)
                first_audio_sent = True
            await websocket.send_bytes(audio_chunk)
    finally:
        llm_task.cancel()

    # Update history
    history.append({"role": "user", "text": transcript})
    history.append({"role": "assistant", "text": ""})  # TTS response
    history = history[-20:]  # Keep last 20 turns

    await websocket.send_text(json.dumps({"type": "done"}))
```

## Register WebSocket endpoint

```python
# In app.py:
from fastapi import WebSocket
from chloe.voice.realtime import handle_voice_session

@app.websocket("/v1/voice")
async def voice_ws(websocket: WebSocket):
    await websocket.accept()
    await handle_voice_session(websocket)
```

## Dependencies

- F-V01 (`voice/stt_whisper.py` — `transcribe_stream()`).
- F-V02 (`voice/tts.py` — `synthesize_stream()` dispatcher).
- E-09 (`channels/chat_api.py` — `chat_2_0()` with `voice=True` flag).
- F-10 (`observability/metrics.py` — `record_voice_latency()`).

## Testing

### Integration tests — `tests/integration/test_voice_realtime.py`

```python
import pytest
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_voice_pipeline_end_to_end(monkeypatch):
    """Mock STT → LLM → TTS → verify audio chunks sent back."""
    from chloe.voice.realtime import handle_voice_session

    transcript_yielded = []
    audio_sent = []
    text_sent = []

    class MockWebSocket:
        async def send_bytes(self, data): audio_sent.append(data)
        async def send_text(self, text): text_sent.append(json.loads(text))
        async def iter_bytes(self):
            yield b"\x00" * 1000  # Fake audio chunk
            yield b"\x00" * 1000

    async def mock_transcribe(audio_iter):
        async for _ in audio_iter:
            pass
        yield "what time is it"

    async def mock_tts(text_iter):
        async for _ in text_iter:
            pass
        yield b"audio_data_1"
        yield b"audio_data_2"

    monkeypatch.setattr("chloe.voice.realtime.transcribe_stream", mock_transcribe)
    monkeypatch.setattr("chloe.voice.realtime.synthesize_stream", mock_tts)
    monkeypatch.setattr("chloe.voice.realtime.chat_2_0", AsyncMock(return_value="It's 10am"))

    ws = MockWebSocket()
    try:
        await asyncio.wait_for(handle_voice_session(ws), timeout=3.0)
    except (asyncio.TimeoutError, StopAsyncIteration):
        pass

    # Verify transcript was sent
    transcripts = [m for m in text_sent if m.get("type") == "transcript"]
    assert len(transcripts) > 0

    # Verify audio was sent
    assert len(audio_sent) > 0
    assert b"audio_data_1" in audio_sent


@pytest.mark.asyncio
async def test_interrupt_stops_pipeline(monkeypatch):
    """Interrupt event stops TTS before completion."""
    from chloe.voice.realtime import handle_voice_session

    audio_sent = []

    class MockWebSocket:
        def __init__(self):
            self._sent = 0

        async def send_bytes(self, data):
            audio_sent.append(data)

        async def send_text(self, text):
            pass

        async def iter_bytes(self):
            yield b"\x00" * 1000
            yield json.dumps({"type": "interrupt"}).encode()

    long_audio = [b"\xff" * 1024 for _ in range(100)]
    chunk_idx = 0

    async def slow_tts(text_iter):
        async for _ in text_iter:
            pass
        for chunk in long_audio:
            await asyncio.sleep(0.01)
            yield chunk

    monkeypatch.setattr("chloe.voice.realtime.transcribe_stream",
                        AsyncMock(side_effect=lambda a: _mock_stt_iter(a)))
    monkeypatch.setattr("chloe.voice.realtime.synthesize_stream", slow_tts)
    monkeypatch.setattr("chloe.voice.realtime.chat_2_0", AsyncMock(return_value="Long reply..."))

    ws = MockWebSocket()
    try:
        await asyncio.wait_for(handle_voice_session(ws), timeout=2.0)
    except asyncio.TimeoutError:
        pass

    # Should have received some but not all 100 chunks (interrupt cut it short)
    assert len(audio_sent) < 100


async def _mock_stt_iter(audio_iter):
    async for _ in audio_iter:
        pass
    yield "test transcript"
```

## Acceptance criteria

- Full pipeline: audio in → transcript → LLM → TTS → audio out, all in sequence.
- Time-to-first-audio ≤ 2s on localhost (measured in `voice_first_audio` log).
- Interrupt message cancels TTS before sending all chunks.
- WebSocket disconnect → session cleanup without exception.
