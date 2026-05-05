import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

from chloe.tools.spotify import SpotifyTool


@pytest.fixture
def tool():
    return SpotifyTool()


def test_queue_track_dry_run(tool):
    preview = tool.dry_run("queue_track", {"uri": "spotify:track:abc123"})
    assert "queue" in preview.lower()
    assert "spotify:track:abc123" in preview


def test_build_playlist_dry_run(tool):
    preview = tool.dry_run("build_playlist", {"name": "My Mix", "track_uris": ["a", "b", "c"]})
    assert "My Mix" in preview
    assert "3" in preview


def test_dry_run_no_api_call(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    with patch("httpx.AsyncClient") as mock_client:
        tool.dry_run("queue_track", {"uri": "spotify:track:x"})
        mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_queue_track_invalid_uri(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    result = await tool.execute("queue_track", {"uri": "not-a-spotify-uri"})
    assert not result.success
    assert "URI" in result.error


@pytest.mark.asyncio
async def test_queue_track_success(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})

    async def mock_post(url, headers=None, params=None):
        resp = MagicMock()
        resp.status_code = 204
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        with patch("chloe.tools.spotify._register_artifact"):
            result = await tool.execute("queue_track", {"uri": "spotify:track:abc"})

    assert result.success


@pytest.mark.asyncio
async def test_artifact_row_after_queue(tool, tmp_path, monkeypatch):
    from chloe.state.db import migrate, close, get_connection
    MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)

    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})

    async def mock_post(url, headers=None, params=None):
        resp = MagicMock()
        resp.status_code = 204
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        result = await tool.execute("queue_track", {"uri": "spotify:track:xyz"})

    assert result.success
    conn = get_connection()
    row = conn.execute("SELECT * FROM artifact_index WHERE ref='spotify:track:xyz'").fetchone()
    assert row is not None
    assert row["kind"] == "spotify_track"
    close()


def test_write_verbs_are_kinetic(tool):
    for verb in ("queue_track", "start_playlist", "like", "skip", "build_playlist"):
        assert tool.verbs[verb].auth_class == "kinetic"
