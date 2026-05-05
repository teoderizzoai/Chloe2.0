import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from chloe.tools.spotify import SpotifyTool

MOCK_CURRENTLY_PLAYING = {
    "item": {
        "name": "Funeral",
        "artists": [{"name": "Phoebe Bridgers"}],
        "uri": "spotify:track:abc",
    },
    "progress_ms": 45000,
    "is_playing": True,
}

MOCK_RECENT = {
    "items": [
        {
            "track": {
                "name": "Savior Complex",
                "artists": [{"name": "Phoebe Bridgers"}],
                "uri": "spotify:track:xyz",
            },
            "played_at": "2026-05-04T10:30:00Z",
        }
    ]
}


@pytest.fixture
def tool():
    return SpotifyTool()


@pytest.mark.asyncio
async def test_show_currently_playing(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    with patch.object(tool, "_call", AsyncMock(return_value=MOCK_CURRENTLY_PLAYING)):
        result = await tool.execute("show_currently_playing", {})
    assert result.success
    assert result.data["track"] == "Funeral"
    assert result.data["artist"] == "Phoebe Bridgers"
    assert result.data["is_playing"] is True


@pytest.mark.asyncio
async def test_show_recent_listens(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    with patch.object(tool, "_call", AsyncMock(return_value=MOCK_RECENT)):
        result = await tool.execute("show_recent_listens", {"limit": 5})
    assert result.success
    assert len(result.data["items"]) == 1
    assert result.data["items"][0]["track"] == "Savior Complex"


@pytest.mark.asyncio
async def test_401_triggers_refresh(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "expired"})
    monkeypatch.setattr("chloe.tools.spotify.refresh_token", AsyncMock(return_value={"access_token": "fresh"}))

    call_count = 0

    async def mock_get(url, headers=None, params=None):
        nonlocal call_count
        call_count += 1
        resp = MagicMock()
        resp.status_code = 401 if call_count == 1 else 200
        resp.json.return_value = MOCK_CURRENTLY_PLAYING
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get
        await tool.execute("show_currently_playing", {})

    assert call_count == 2


@pytest.mark.asyncio
async def test_no_token_returns_not_playing(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: None)
    result = await tool.execute("show_currently_playing", {})
    assert not result.success or result.data.get("is_playing") is False


def test_all_verbs_are_intimate(tool):
    for verb in tool.verbs.values():
        assert verb.auth_class == "intimate"
