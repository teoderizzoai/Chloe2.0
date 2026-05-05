# C-01 · `tools/spotify.py` — write verbs

## Overview

Add write verbs to `chloe/tools/spotify.py`: `queue_track(uri)`, `start_playlist(uri)`, `like(uri)`, `skip()`, `build_playlist(name, description, track_uris)`. Auth: `kinetic`. Each write registers an `artifact_index` row on success.

## Write verbs

```python
# Add to SpotifyTool.verbs in __init__:

"queue_track": ToolVerb(
    name="queue_track",
    schema={"type":"object","properties":{"uri":{"type":"string"}},"required":["uri"]},
    auth_class="kinetic", reversibility=0.6,
    cost_per_call_usd=0.0,
    description_for_model="Queue a track on Teo's Spotify. Uses the 'spotify:track:...' URI.",
    description_for_human="Queue track",
    reverse_verb="skip",
),
"start_playlist": ToolVerb(..., auth_class="kinetic", reversibility=0.3, ...),
"like": ToolVerb(..., auth_class="kinetic", reversibility=0.9, ...),
"skip": ToolVerb(..., auth_class="kinetic", reversibility=0.0, ...),
"build_playlist": ToolVerb(..., auth_class="kinetic", reversibility=0.8, ...),
```

## Artifact registration

After each successful write, call `_register_artifact()`:

```python
def _register_artifact(kind: str, ref: str, title: str, action_id: str) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO artifact_index (kind, ref, title, created_by_action, exists_)
        VALUES (?, ?, ?, ?, 1)
        """,
        (kind, ref, title, action_id),
    )
    conn.commit()
```

For `queue_track`: `kind="spotify_track"`, `ref=uri`.
For `build_playlist`: `kind="spotify_playlist"`, `ref=playlist_uri`.

## Execute implementation (write verbs)

```python
elif verb == "queue_track":
    uri = args.get("uri", "")
    if not uri.startswith("spotify:track:"):
        return ToolResult(success=False, error="Invalid Spotify track URI")
    
    hdrs = await self._get_headers()
    if not hdrs:
        return ToolResult(success=False, error="No Spotify token")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{SPOTIFY_API}/me/player/queue",
            headers=hdrs,
            params={"uri": uri},
        )
    
    if resp.status_code in (200, 204):
        _register_artifact("spotify_track", uri, uri.split(":")[-1], action.id)
        return ToolResult(success=True, data={"queued": uri}, artifact_ref=uri, artifact_kind="spotify_track")
    return ToolResult(success=False, error=f"Spotify API error: {resp.status_code}")
```

Note: `action.id` is not available in `execute(verb, args)` — pass it as `args["_action_id"]` or through a separate `set_current_action_id()` method. Design choice: pass `action_id` in args as a reserved key `"__action_id"` that the gate injects.

## Dry-run behaviour

```python
def dry_run(self, verb: str, args: dict) -> str:
    if verb == "queue_track":
        return f"Would queue track: {args.get('uri', '?')}"
    if verb == "build_playlist":
        return f"Would create playlist '{args.get('name', '?')}' with {len(args.get('track_uris', []))} tracks"
    return super().dry_run(verb, args)
```

## Dependencies

- B-04 (existing SpotifyTool read verbs, token refresh).
- A-01 (`artifact_index` table).
- A-06 (Tool base).

## Testing

### Unit tests — `tests/unit/test_spotify_write.py`

```python
import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
import httpx
from chloe.tools.spotify import SpotifyTool

@pytest.fixture
def tool():
    return SpotifyTool()

@pytest.mark.asyncio
async def test_queue_track_dry_run(tool):
    preview = tool.dry_run("queue_track", {"uri": "spotify:track:abc123"})
    assert "queue" in preview.lower()
    assert "spotify:track:abc123" in preview

@pytest.mark.asyncio
async def test_queue_track_success(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    
    async def mock_post(url, headers=None, params=None):
        resp = MagicMock()
        resp.status_code = 204
        return resp
    
    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        # Patch artifact registration
        with patch("chloe.tools.spotify._register_artifact") as mock_reg:
            result = await tool.execute("queue_track", {"uri": "spotify:track:abc"})
    
    assert result.success

@pytest.mark.asyncio
async def test_queue_track_invalid_uri(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    result = await tool.execute("queue_track", {"uri": "not-a-spotify-uri"})
    assert not result.success
    assert "URI" in result.error

def test_dry_run_no_api_call(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    # dry_run should never call any API
    with patch("httpx.AsyncClient") as mock_client:
        tool.dry_run("queue_track", {"uri": "spotify:track:x"})
        mock_client.assert_not_called()

@pytest.mark.asyncio
async def test_artifact_row_after_queue(tool, tmp_path, monkeypatch):
    """After successful queue_track, artifact_index has a row."""
    from pathlib import Path
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
        result = await tool.execute("queue_track", {"uri": "spotify:track:xyz", "__action_id": "act1"})
    
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM artifact_index WHERE ref='spotify:track:xyz'"
    ).fetchone()
    assert row is not None
    assert row["kind"] == "spotify_track"
    close()
```

## Acceptance criteria

- `dry_run("queue_track", ...)` returns correct preview without hitting Spotify.
- VCR cassette test for `queue_track` verifies the POST to `/me/player/queue`.
- Artifact row appears in `artifact_index` after successful execute.
- Invalid URI returns error without API call.
