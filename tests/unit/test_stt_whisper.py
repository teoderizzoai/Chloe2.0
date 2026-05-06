"""Tests for voice/stt_whisper.py (F-V01)."""
from __future__ import annotations

import asyncio
import io
import math
import struct
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_wav_bytes(duration_ms: int = 200, sample_rate: int = 16000) -> bytes:
    n = int(sample_rate * duration_ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        frames = b"".join(
            struct.pack("<h", int(32767 * math.sin(2 * math.pi * 440 * i / sample_rate)))
            for i in range(n)
        )
        wf.writeframes(frames)
    return buf.getvalue()


async def _as_iter(chunks):
    for c in chunks:
        yield c


class TestTranscribeStream:
    async def test_local_whisper_mock_transcript(self, monkeypatch):
        """Mocked _run_whisper emits result from transcribe_stream."""
        monkeypatch.setenv("WHISPER_MODE", "local")
        import chloe.voice.stt_whisper as mod

        monkeypatch.setattr(mod, "_run_whisper", lambda _: "hello world")

        wav_bytes = _make_wav_bytes()
        results = []
        async for partial in mod.transcribe_stream(_as_iter([wav_bytes])):
            results.append(partial)

        assert results == ["hello world"]

    async def test_local_empty_audio_yields_nothing(self, monkeypatch):
        monkeypatch.setenv("WHISPER_MODE", "local")
        import chloe.voice.stt_whisper as mod

        results = []
        async for partial in mod.transcribe_stream(_as_iter([])):
            results.append(partial)

        assert results == []

    async def test_whisper_empty_transcript_yields_nothing(self, monkeypatch):
        monkeypatch.setenv("WHISPER_MODE", "local")
        import chloe.voice.stt_whisper as mod

        monkeypatch.setattr(mod, "_run_whisper", lambda _: "")

        results = []
        async for partial in mod.transcribe_stream(_as_iter([b"\x00" * 100])):
            results.append(partial)

        assert results == []

    async def test_silence_timeout_fires(self, monkeypatch):
        """Slow stream hits the silence timeout; whatever audio was collected is transcribed."""
        monkeypatch.setenv("WHISPER_MODE", "local")
        import chloe.voice.stt_whisper as mod

        calls = []

        def _fake_run_whisper(audio: bytes) -> str:
            calls.append(len(audio))
            return "partial"

        monkeypatch.setattr(mod, "_run_whisper", _fake_run_whisper)

        async def _slow_stream():
            yield b"\x00\x00" * 100
            await asyncio.sleep(10)  # will be interrupted by timeout
            yield b"\x00\x00" * 100  # never reached

        results = []
        async for partial in mod.transcribe_stream(_slow_stream(), silence_timeout=0.05):
            results.append(partial)

        # Timeout fires; whisper ran on the first chunk only
        assert results == ["partial"]
        assert calls == [200]  # 100 × 2 bytes

    async def test_deepgram_mode_calls_api(self, monkeypatch):
        """Deepgram mode POSTs audio and returns the transcript."""
        monkeypatch.setenv("WHISPER_MODE", "deepgram")
        monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

        import chloe.voice.stt_whisper as mod

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "results": {
                "channels": [{"alternatives": [{"transcript": "deepgram result"}]}]
            }
        }
        fake_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_response

        import httpx

        with patch.object(httpx, "AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = []
            async for partial in mod.transcribe_stream(_as_iter([b"\x00" * 100])):
                results.append(partial)

        assert results == ["deepgram result"]

    async def test_deepgram_no_api_key_yields_nothing(self, monkeypatch):
        monkeypatch.setenv("WHISPER_MODE", "deepgram")
        monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
        import os
        os.environ.pop("DEEPGRAM_API_KEY", None)

        import chloe.voice.stt_whisper as mod

        results = []
        async for partial in mod.transcribe_stream(_as_iter([b"\x00" * 100])):
            results.append(partial)

        assert results == []

    async def test_deepgram_multiple_chunks_collected(self, monkeypatch):
        """All chunks are collected into one payload before the API call."""
        monkeypatch.setenv("WHISPER_MODE", "deepgram")
        monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

        import chloe.voice.stt_whisper as mod

        received_sizes: list[int] = []
        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {
            "results": {"channels": [{"alternatives": [{"transcript": "combined"}]}]}
        }
        fake_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post.return_value = fake_response

        async def _mock_post(url, content, headers):
            received_sizes.append(len(content))
            return fake_response

        mock_client.post.side_effect = _mock_post

        import httpx

        with patch.object(httpx, "AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            results = []
            async for p in mod.transcribe_stream(_as_iter([b"aaa", b"bbb"])):
                results.append(p)

        assert results == ["combined"]
        assert received_sizes == [6]  # len(b"aaa") + len(b"bbb")
