import httpx

from chloe.observability.logging import get_logger
from chloe.state.oauth_tokens import load as load_token
from chloe.state.oauth_tokens import refresh as refresh_token
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.spotify")
SPOTIFY_API = "https://api.spotify.com/v1"


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

        return ToolResult(success=False, error=f"Unknown verb: {verb}")
