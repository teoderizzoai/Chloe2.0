import httpx

from chloe.observability.logging import get_logger
from chloe.state.oauth_tokens import load as load_token
from chloe.state.oauth_tokens import refresh as refresh_token
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.spotify")
SPOTIFY_API = "https://api.spotify.com/v1"


def _register_artifact(kind: str, ref: str, title: str, action_id: str) -> None:
    from chloe.state.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO artifact_index (kind, ref, title, created_by_action, exists_) VALUES (?, ?, ?, ?, 1)",
        (kind, ref, title, action_id),
    )
    conn.commit()


class SpotifyTool(Tool):
    name = "spotify"

    def __init__(self):
        self.verbs = {
            "show_currently_playing": ToolVerb(
                name="show_currently_playing",
                schema={"type": "object", "properties": {}},
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Show what Teo is currently playing on Spotify.",
                description_for_human="Show currently playing",
            ),
            "show_recent_listens": ToolVerb(
                name="show_recent_listens",
                schema={"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}},
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Show Teo's recently played tracks on Spotify.",
                description_for_human="Show recent listens",
            ),
            "search": ToolVerb(
                name="search",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Free-text query, e.g. 'Traumatized Chri$tian Gate$'"},
                        "type": {"type": "string", "description": "track | album | playlist | artist", "default": "track"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
                auth_class="intimate",
                reversibility=1.0,
                description_for_model=(
                    "Search Spotify's catalog for tracks/albums/playlists/artists by name. "
                    "Use this to resolve a song name to a 'spotify:track:...' URI before queueing or playing."
                ),
                description_for_human="Search Spotify",
            ),
            "play_track": ToolVerb(
                name="play_track",
                schema={"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"]},
                auth_class="kinetic",
                reversibility=0.3,
                cost_per_call_usd=0.0,
                description_for_model=(
                    "Play a single track immediately on Teo's Spotify (interrupts current playback). "
                    "Takes a 'spotify:track:...' URI. Use this when Teo says 'play X now' — not queue_track."
                ),
                description_for_human="Play track now",
            ),
            "queue_track": ToolVerb(
                name="queue_track",
                schema={"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"]},
                auth_class="kinetic",
                reversibility=0.6,
                cost_per_call_usd=0.0,
                description_for_model="Queue a track on Teo's Spotify. Uses the 'spotify:track:...' URI.",
                description_for_human="Queue track",
                reverse_verb="clear_queue",
            ),
            "start_playlist": ToolVerb(
                name="start_playlist",
                schema={"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"]},
                auth_class="kinetic",
                reversibility=0.3,
                cost_per_call_usd=0.0,
                description_for_model="Start playing a playlist on Teo's Spotify.",
                description_for_human="Start playlist",
            ),
            "like": ToolVerb(
                name="like",
                schema={"type": "object", "properties": {"uri": {"type": "string"}}, "required": ["uri"]},
                auth_class="kinetic",
                reversibility=0.9,
                cost_per_call_usd=0.0,
                description_for_model="Like a track on Teo's Spotify.",
                description_for_human="Like track",
            ),
            "skip": ToolVerb(
                name="skip",
                schema={"type": "object", "properties": {}},
                auth_class="kinetic",
                reversibility=0.0,
                cost_per_call_usd=0.0,
                description_for_model="Skip the current track on Teo's Spotify.",
                description_for_human="Skip track",
            ),
            "build_playlist": ToolVerb(
                name="build_playlist",
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "track_uris": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "track_uris"],
                },
                auth_class="kinetic",
                reversibility=0.8,
                cost_per_call_usd=0.0,
                description_for_model="Create a new Spotify playlist with the given tracks.",
                description_for_human="Build playlist",
            ),
            "clear_queue": ToolVerb(
                name="clear_queue",
                schema={"type": "object", "properties": {}},
                auth_class="kinetic",
                reversibility=0.0,
                cost_per_call_usd=0.0,
                description_for_model="Skip current track and clear the queue. Best-effort undo for queue_track.",
                description_for_human="Clear queue",
            ),
        }

    def dry_run(self, verb: str, args: dict) -> str:
        if verb == "queue_track":
            return f"Would queue track: {args.get('uri', '?')}"
        if verb == "play_track":
            return f"Would play track now: {args.get('uri', '?')}"
        if verb == "build_playlist":
            return f"Would create playlist '{args.get('name', '?')}' with {len(args.get('track_uris', []))} tracks"
        if verb == "start_playlist":
            return f"Would start playlist: {args.get('uri', '?')}"
        if verb == "like":
            return f"Would like track: {args.get('uri', '?')}"
        if verb == "skip":
            return "Would skip current track"
        if verb == "clear_queue":
            return "Would clear queue (skip current track)"
        return super().dry_run(verb, args)

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
                new_token = await refresh_token("spotify")
                if new_token:
                    headers = {"Authorization": f"Bearer {new_token['access_token']}"}
                    resp = await client.get(f"{SPOTIFY_API}{path}", headers=headers, params=params)

            if resp.status_code == 204:
                return None
            if resp.status_code != 200:
                log.error("spotify_api_error", path=path, status=resp.status_code)
                return None
            return resp.json()

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb == "search":
            q = args.get("query", "").strip()
            if not q:
                return ToolResult(success=False, error="query is required")
            stype = args.get("type", "track")
            limit = min(int(args.get("limit", 5) or 5), 20)
            data = await self._call("/search", params={"q": q, "type": stype, "limit": limit})
            if not data:
                return ToolResult(success=False, error="Spotify search failed")
            key = stype + "s"
            items = (data.get(key) or {}).get("items", [])
            results = []
            for it in items:
                results.append({
                    "uri": it.get("uri"),
                    "name": it.get("name"),
                    "artists": ", ".join(a["name"] for a in it.get("artists", [])) if it.get("artists") else None,
                    "album": (it.get("album") or {}).get("name") if stype == "track" else None,
                })
            return ToolResult(success=True, data={"results": results})

        if verb == "play_track":
            uri = args.get("uri", "")
            if not uri.startswith("spotify:track:"):
                return ToolResult(success=False, error="Invalid Spotify track URI")
            hdrs = await self._get_headers()
            if not hdrs:
                return ToolResult(success=False, error="No Spotify token")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.put(
                    f"{SPOTIFY_API}/me/player/play",
                    headers=hdrs,
                    json={"uris": [uri]},
                )
            if resp.status_code in (200, 204):
                return ToolResult(success=True, data={"playing": uri}, artifact_ref=uri, artifact_kind="spotify_track")
            return ToolResult(success=False, error=f"Spotify API error: {resp.status_code}: {resp.text[:200]}")

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

        if verb == "show_recent_listens":
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

        if verb == "queue_track":
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
                _register_artifact("spotify_track", uri, uri.split(":")[-1], args.get("__action_id") or None)
                return ToolResult(success=True, data={"queued": uri}, artifact_ref=uri, artifact_kind="spotify_track")
            return ToolResult(success=False, error=f"Spotify API error: {resp.status_code}")

        if verb == "start_playlist":
            uri = args.get("uri", "")
            hdrs = await self._get_headers()
            if not hdrs:
                return ToolResult(success=False, error="No Spotify token")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.put(
                    f"{SPOTIFY_API}/me/player/play",
                    headers=hdrs,
                    json={"context_uri": uri},
                )
            if resp.status_code in (200, 204):
                return ToolResult(success=True, data={"started": uri})
            return ToolResult(success=False, error=f"Spotify API error: {resp.status_code}")

        if verb == "like":
            uri = args.get("uri", "")
            track_id = uri.split(":")[-1] if ":" in uri else uri
            hdrs = await self._get_headers()
            if not hdrs:
                return ToolResult(success=False, error="No Spotify token")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.put(
                    f"{SPOTIFY_API}/me/tracks",
                    headers=hdrs,
                    params={"ids": track_id},
                )
            if resp.status_code in (200, 204):
                return ToolResult(success=True, data={"liked": uri})
            return ToolResult(success=False, error=f"Spotify API error: {resp.status_code}")

        if verb == "skip":
            hdrs = await self._get_headers()
            if not hdrs:
                return ToolResult(success=False, error="No Spotify token")
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{SPOTIFY_API}/me/player/next", headers=hdrs)
            if resp.status_code in (200, 204):
                return ToolResult(success=True, data={"skipped": True})
            return ToolResult(success=False, error=f"Spotify API error: {resp.status_code}")

        if verb == "build_playlist":
            hdrs = await self._get_headers()
            if not hdrs:
                return ToolResult(success=False, error="No Spotify token")
            # Get user ID first
            async with httpx.AsyncClient(timeout=10.0) as client:
                me_resp = await client.get(f"{SPOTIFY_API}/me", headers=hdrs)
            if me_resp.status_code != 200:
                return ToolResult(success=False, error="Failed to get Spotify user ID")
            user_id = me_resp.json().get("id", "")
            # Create playlist
            async with httpx.AsyncClient(timeout=10.0) as client:
                create_resp = await client.post(
                    f"{SPOTIFY_API}/users/{user_id}/playlists",
                    headers=hdrs,
                    json={"name": args["name"], "description": args.get("description", ""), "public": False},
                )
            if create_resp.status_code not in (200, 201):
                return ToolResult(success=False, error=f"Spotify API error: {create_resp.status_code}")
            playlist = create_resp.json()
            playlist_id = playlist.get("id", "")
            playlist_uri = playlist.get("uri", f"spotify:playlist:{playlist_id}")
            # Add tracks
            track_uris = args.get("track_uris", [])
            if track_uris:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    await client.post(
                        f"{SPOTIFY_API}/playlists/{playlist_id}/tracks",
                        headers=hdrs,
                        json={"uris": track_uris},
                    )
            _register_artifact("spotify_playlist", playlist_uri, args["name"], args.get("__action_id") or None)
            return ToolResult(
                success=True,
                data={"playlist_id": playlist_id, "playlist_uri": playlist_uri, "name": args["name"]},
                artifact_ref=playlist_uri,
                artifact_kind="spotify_playlist",
            )

        if verb == "clear_queue":
            hdrs = await self._get_headers()
            if not hdrs:
                return ToolResult(success=False, error="No Spotify token")
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(f"{SPOTIFY_API}/me/player/next", headers=hdrs)
            return ToolResult(success=True, data={"cleared": True})

        return ToolResult(success=False, error=f"Unknown verb: {verb}")
