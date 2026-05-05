# B-04 · `tools/spotify.py` — read verbs

## Overview

Implement `chloe/tools/spotify.py` with read-only verbs: `show_currently_playing()` and `show_recent_listens(limit=10)`. Auth: `intimate`. Uses `httpx.AsyncClient` with the stored Spotify token. Handles 401 by triggering `oauth_tokens.refresh("spotify")`.

## Context

Read verbs let the chat prompt include what Teo is listening to — enabling morning messages like "I see you've been listening to Phoebe Bridgers — I queued her new album." They are `intimate` auth: they access a real account but only read, and Teo expects Chloe to know what he's listening to.

## Implementation

```python
# chloe/tools/spotify.py

from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.state.oauth_tokens import load as load_token, refresh as refresh_token
from chloe.observability.logging import get_logger
import httpx

log = get_logger("tool.spotify")
SPOTIFY_API = "https://api.spotify.com/v1"

class SpotifyTool(Tool):
    name = "spotify"

    def __init__(self):
        self.verbs = {
            "show_currently_playing": ToolVerb(
                name="show_currently_playing",
                schema={"type": "object", "properties": {}},
                auth_class="intimate", reversibility=1.0,
                description_for_model="Show what Teo is currently playing on Spotify.",
                description_for_human="Show currently playing",
            ),
            "show_recent_listens": ToolVerb(
                name="show_recent_listens",
                schema={"type":"object","properties":{"limit":{"type":"integer","default":10}}},
                auth_class="intimate", reversibility=1.0,
                description_for_model="Show Teo's recently played tracks on Spotify.",
                description_for_human="Show recent listens",
            ),
        }

    async def _get_headers(self) -> dict | None:
        token = load_token("spotify")
        if not token:
            return None
        return {"Authorization": f"Bearer {token['access_token']}"}

    async def _call(self, path: str, params: dict | None = None) -> dict | None:
        headers = await self._get_headers()
        if not headers:
            return None
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{SPOTIFY_API}{path}", headers=headers, params=params)
            
            if resp.status_code == 401:
                # Refresh and retry once
                new_token = await refresh_token("spotify")
                if new_token:
                    headers = {"Authorization": f"Bearer {new_token['access_token']}"}
                    resp = await client.get(f"{SPOTIFY_API}{path}", headers=headers, params=params)
            
            if resp.status_code == 204:  # No content (nothing playing)
                return None
            if resp.status_code != 200:
                log.error("spotify_api_error", path=path, status=resp.status_code)
                return None
            return resp.json()

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb == "show_currently_playing":
            data = await self._call("/me/player/currently-playing")
            if not data or not data.get("item"):
                return ToolResult(success=True, data={"is_playing": False, "track": None})
            item = data["item"]
            return ToolResult(success=True, data={
                "track": item.get("name"),
                "artist": ", ".join(a["name"] for a in item.get("artists", [])),
                "progress_ms": data.get("progress_ms", 0),
                "is_playing": data.get("is_playing", False),
                "uri": item.get("uri"),
            })

        elif verb == "show_recent_listens":
            limit = min(args.get("limit", 10), 50)
            data = await self._call("/me/player/recently-played", params={"limit": limit})
            if not data:
                return ToolResult(success=False, error="Failed to fetch recent listens")
            items = [
                {
                    "track": item["track"]["name"],
                    "artist": ", ".join(a["name"] for a in item["track"]["artists"]),
                    "played_at": item["played_at"],
                    "uri": item["track"]["uri"],
                }
                for item in data.get("items", [])
            ]
            return ToolResult(success=True, data={"items": items})

        return ToolResult(success=False, error=f"Unknown verb: {verb}")
```

## Dependencies

- A-06 (Tool base).
- B-01 (`load_token`, `refresh_token`).
- F-09 (logging).

## Testing

### VCR cassette test — `tests/unit/test_spotify_read.py`

```python
import pytest
import asyncio
import json
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
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
            "track": {"name": "Savior Complex", "artists": [{"name": "Phoebe Bridgers"}], "uri": "spotify:track:xyz"},
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
    
    async def mock_get(url, headers=None, params=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = MOCK_CURRENTLY_PLAYING
        return resp
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get
        result = await tool.execute("show_currently_playing", {})
    
    assert result.success
    assert result.data["track"] == "Funeral"
    assert result.data["artist"] == "Phoebe Bridgers"
    assert result.data["is_playing"] is True

@pytest.mark.asyncio
async def test_show_recent_listens(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    
    async def mock_get(url, headers=None, params=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = MOCK_RECENT
        return resp
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get
        result = await tool.execute("show_recent_listens", {"limit": 5})
    
    assert result.success
    assert len(result.data["items"]) == 1
    assert result.data["items"][0]["track"] == "Savior Complex"

@pytest.mark.asyncio
async def test_401_triggers_refresh(tool, monkeypatch):
    calls = []
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "expired"})
    monkeypatch.setattr(
        "chloe.tools.spotify.refresh_token",
        AsyncMock(return_value={"access_token": "fresh"})
    )
    
    response_count = 0
    async def mock_get(url, headers=None, params=None):
        nonlocal response_count
        response_count += 1
        resp = MagicMock()
        resp.status_code = 401 if response_count == 1 else 200
        resp.json.return_value = MOCK_CURRENTLY_PLAYING
        return resp
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = mock_get
        result = await tool.execute("show_currently_playing", {})
    
    assert response_count == 2   # first 401, then retry

@pytest.mark.asyncio
async def test_no_token_fails_gracefully(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: None)
    result = await tool.execute("show_currently_playing", {})
    assert not result.success or result.data.get("is_playing") is False
```

### Integration test (`@pytest.mark.live`)

```python
@pytest.mark.live
@pytest.mark.asyncio
async def test_real_spotify_currently_playing():
    tool = SpotifyTool()
    result = await tool.execute("show_currently_playing", {})
    assert result.success
    # track may be None if nothing playing, that's OK
```

## Acceptance criteria

- VCR cassette test passes.
- 401 triggers `refresh_token("spotify")` and retries.
- Integration test (`@pytest.mark.live`) passes against real Spotify.
- Result shape: `{track, artist, progress_ms, is_playing}` for currently_playing; `list[{track, artist, played_at}]` for recent_listens.
