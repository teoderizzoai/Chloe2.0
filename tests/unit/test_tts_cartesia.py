"""Tests for voice/tts_cartesia.py and voice/tts_elevenlabs.py (F-V02)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


async def _as_iter(items):
    for i in items:
        yield i


class TestCartesiaSynthesize:
    async def test_streams_audio_chunks(self, monkeypatch):
        """Mocked Cartesia response yields PCM bytes chunks."""
        monkeypatch.setenv("CARTESIA_API_KEY", "test-key")
        monkeypatch.setenv("CARTESIA_VOICE_ID", "voice-abc")

        import chloe.voice.tts_cartesia as mod

        chunk1 = b"\x00\x01" * 512
        chunk2 = b"\x02\x03" * 512

        async def _fake_aiter_bytes(chunk_size=None):
            yield chunk1
            yield chunk2

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = _fake_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_stream_ctx

        import httpx

        with patch.object(httpx, "AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            chunks = []
            async for c in mod.synthesize_stream(_as_iter(["hello ", "world"])):
                chunks.append(c)

        assert chunks == [chunk1, chunk2]

    async def test_no_api_key_yields_nothing(self, monkeypatch):
        monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
        import os
        os.environ.pop("CARTESIA_API_KEY", None)
        import chloe.voice.tts_cartesia as mod

        chunks = []
        async for c in mod.synthesize_stream(_as_iter(["hello"]), api_key=None):
            chunks.append(c)
        assert chunks == []

    async def test_explicit_api_key_param(self, monkeypatch):
        """api_key param takes precedence over env var."""
        monkeypatch.delenv("CARTESIA_API_KEY", raising=False)
        import chloe.voice.tts_cartesia as mod

        async def _fake_aiter_bytes(chunk_size=None):
            yield b"\xaa\xbb"

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = _fake_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_stream_ctx

        import httpx

        with patch.object(httpx, "AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            chunks = []
            async for c in mod.synthesize_stream(
                _as_iter(["hi"]), api_key="explicit-key", voice_id="v1"
            ):
                chunks.append(c)

        assert b"\xaa\xbb" in chunks

    async def test_empty_text_yields_nothing(self, monkeypatch):
        monkeypatch.setenv("CARTESIA_API_KEY", "test-key")
        import chloe.voice.tts_cartesia as mod

        chunks = []
        async for c in mod.synthesize_stream(_as_iter(["   "])):
            chunks.append(c)
        assert chunks == []

    async def test_cartesia_error_yields_nothing(self, monkeypatch):
        monkeypatch.setenv("CARTESIA_API_KEY", "test-key")
        import chloe.voice.tts_cartesia as mod

        mock_client = MagicMock()
        mock_client.stream.side_effect = Exception("connection error")

        import httpx

        with patch.object(httpx, "AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            chunks = []
            async for c in mod.synthesize_stream(_as_iter(["hello"])):
                chunks.append(c)

        assert chunks == []


class TestElevenLabsSynthesize:
    async def test_streams_audio_chunks(self, monkeypatch):
        """ElevenLabs adapter follows the same interface as Cartesia."""
        monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
        monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice-xyz")

        import chloe.voice.tts_elevenlabs as mod

        chunk1 = b"\x10\x20" * 256

        async def _fake_aiter_bytes(chunk_size=None):
            yield chunk1

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.aiter_bytes = _fake_aiter_bytes

        mock_stream_ctx = AsyncMock()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.stream.return_value = mock_stream_ctx

        import httpx

        with patch.object(httpx, "AsyncClient") as mock_cls:
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            chunks = []
            async for c in mod.synthesize_stream(_as_iter(["hello"])):
                chunks.append(c)

        assert chunks == [chunk1]

    async def test_no_api_key_yields_nothing(self, monkeypatch):
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        import os
        os.environ.pop("ELEVENLABS_API_KEY", None)
        import chloe.voice.tts_elevenlabs as mod

        chunks = []
        async for c in mod.synthesize_stream(_as_iter(["hello"]), api_key=None):
            chunks.append(c)
        assert chunks == []
