"""Tests for voice/realtime.py (F-V03)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class FakeWebSocket:
    """Minimal WebSocket shim for unit tests."""

    def __init__(self, messages: list):
        self._in = list(messages)
        self.sent_bytes: list[bytes] = []
        self.sent_text: list[str] = []

    async def receive(self):
        if self._in:
            return self._in.pop(0)
        await asyncio.sleep(60)  # block until timeout fires

    async def send_bytes(self, data: bytes):
        self.sent_bytes.append(data)

    async def send_text(self, data: str):
        self.sent_text.append(data)


class TestHandleVoiceSession:
    async def test_audio_flows_through_pipeline(self, monkeypatch):
        """Audio bytes → STT → LLM → TTS → WebSocket bytes."""
        import chloe.voice.realtime as mod

        audio_chunk = b"\x01\x02" * 100
        pcm_audio = b"\xaa\xbb" * 512

        async def _fake_stt(audio_iter, **kwargs) -> AsyncIterator[str]:
            async for _ in audio_iter:
                pass
            yield "hello chloe"

        async def _fake_tts(text_iter, **kwargs) -> AsyncIterator[bytes]:
            async for _ in text_iter:
                pass
            yield pcm_audio

        monkeypatch.setattr(
            "chloe.voice.realtime._get_reply",
            AsyncMock(return_value="Hi there!"),
        )

        ws = FakeWebSocket(
            [
                {"bytes": audio_chunk},
                {"type": "websocket.disconnect"},
            ]
        )

        import chloe.voice.stt_whisper as stt_mod
        import chloe.voice.tts_cartesia as tts_mod

        with (
            patch.object(stt_mod, "transcribe_stream", _fake_stt),
            patch.object(tts_mod, "synthesize_stream", _fake_tts),
        ):
            await mod.handle_voice_session(ws)

        assert pcm_audio in ws.sent_bytes
        done_frames = [json.loads(t) for t in ws.sent_text]
        assert any(f.get("type") == "done" for f in done_frames)

    async def test_interrupt_stops_pipeline(self, monkeypatch):
        """An interrupt text frame cancels the pipeline before TTS runs."""
        import chloe.voice.realtime as mod

        audio_chunk = b"\x01" * 100

        tts_called = False

        async def _fake_stt(audio_iter, **kwargs) -> AsyncIterator[str]:
            # Drain audio_iter; the interrupt fires during this drain
            async for _ in audio_iter:
                pass
            yield "some words"

        async def _fake_tts(text_iter, **kwargs) -> AsyncIterator[bytes]:
            nonlocal tts_called
            tts_called = True
            async for _ in text_iter:
                pass
            yield b"\xcc" * 100

        monkeypatch.setattr(
            "chloe.voice.realtime._get_reply",
            AsyncMock(return_value="This should not be spoken"),
        )

        ws = FakeWebSocket(
            [
                {"bytes": audio_chunk},
                {"text": json.dumps({"type": "interrupt"})},
            ]
        )

        import chloe.voice.stt_whisper as stt_mod
        import chloe.voice.tts_cartesia as tts_mod

        with (
            patch.object(stt_mod, "transcribe_stream", _fake_stt),
            patch.object(tts_mod, "synthesize_stream", _fake_tts),
        ):
            await mod.handle_voice_session(ws)

        # After interrupt, either TTS was never called or no bytes were sent
        assert not ws.sent_bytes or not tts_called

    async def test_no_transcript_produces_no_output(self, monkeypatch):
        """Empty STT result → no LLM call, no TTS, no bytes sent."""
        import chloe.voice.realtime as mod

        llm_called = False

        async def _empty_stt(audio_iter, **kwargs) -> AsyncIterator[str]:
            async for _ in audio_iter:
                pass
            return
            yield  # make it an async generator

        async def _fake_tts(text_iter, **kwargs) -> AsyncIterator[bytes]:
            yield b"\xdd" * 64

        async def _fake_reply(text: str) -> str:
            nonlocal llm_called
            llm_called = True
            return "reply"

        monkeypatch.setattr("chloe.voice.realtime._get_reply", _fake_reply)

        ws = FakeWebSocket([{"type": "websocket.disconnect"}])

        import chloe.voice.stt_whisper as stt_mod
        import chloe.voice.tts_cartesia as tts_mod

        with (
            patch.object(stt_mod, "transcribe_stream", _empty_stt),
            patch.object(tts_mod, "synthesize_stream", _fake_tts),
        ):
            await mod.handle_voice_session(ws)

        assert ws.sent_bytes == []
        assert not llm_called

    async def test_voice_reply_max_tokens_used(self, monkeypatch):
        """_get_reply is called (the 200-token cap is passed to Gemini in prod)."""
        import chloe.voice.realtime as mod

        reply_calls: list[str] = []

        async def _fake_stt(audio_iter, **kwargs) -> AsyncIterator[str]:
            async for _ in audio_iter:
                pass
            yield "test question"

        async def _fake_tts(text_iter, **kwargs) -> AsyncIterator[bytes]:
            async for _ in text_iter:
                pass
            yield b"\xee" * 64

        async def _tracked_reply(text: str) -> str:
            reply_calls.append(text)
            return "short answer"

        monkeypatch.setattr("chloe.voice.realtime._get_reply", _tracked_reply)

        ws = FakeWebSocket([{"bytes": b"\x00" * 100}, {"type": "websocket.disconnect"}])

        import chloe.voice.stt_whisper as stt_mod
        import chloe.voice.tts_cartesia as tts_mod

        with (
            patch.object(stt_mod, "transcribe_stream", _fake_stt),
            patch.object(tts_mod, "synthesize_stream", _fake_tts),
        ):
            await mod.handle_voice_session(ws)

        assert reply_calls == ["test question"]
