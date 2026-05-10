"""Maps tool — geocoding and routing via free public APIs.

- Geocoding: Nominatim (OpenStreetMap). Polite-use rules apply: descriptive
  User-Agent, ≤1 req/sec. We add a small backoff and a custom UA.
- Routing: OSRM public server (router.project-osrm.org).
"""
from __future__ import annotations

import asyncio

import httpx

from chloe.observability.logging import get_logger
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.maps")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_URL = "https://router.project-osrm.org/route/v1"
USER_AGENT = "ChloeBot/2.0 (personal-assistant)"

_last_geocode_ts: float = 0.0


async def _geocode(place: str, limit: int = 5) -> list[dict]:
    global _last_geocode_ts
    elapsed = asyncio.get_event_loop().time() - _last_geocode_ts
    if elapsed < 1.0:
        await asyncio.sleep(1.0 - elapsed)
    async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": USER_AGENT}) as client:
        resp = await client.get(NOMINATIM_URL, params={
            "q": place, "format": "json", "limit": limit, "addressdetails": 1,
        })
        resp.raise_for_status()
        data = resp.json()
    _last_geocode_ts = asyncio.get_event_loop().time()
    return data


class MapsTool(Tool):
    name = "maps"

    def __init__(self):
        self.verbs = {
            "geocode": ToolVerb(
                name="geocode",
                schema={
                    "type": "object",
                    "properties": {"place": {"type": "string"}, "limit": {"type": "integer"}},
                    "required": ["place"],
                },
                auth_class="free",
                reversibility=1.0,
                cost_per_call_usd=0.0,
                description_for_model="Geocode a place name (street, city, landmark) to lat/lon.",
                description_for_human="Place lookup",
            ),
            "directions": ToolVerb(
                name="directions",
                schema={
                    "type": "object",
                    "properties": {
                        "from_place": {"type": "string"},
                        "to_place": {"type": "string"},
                        "mode": {"type": "string", "enum": ["car", "bike", "foot"]},
                    },
                    "required": ["from_place", "to_place"],
                },
                auth_class="free",
                reversibility=1.0,
                cost_per_call_usd=0.0,
                description_for_model="Get distance and duration between two places.",
                description_for_human="Directions",
            ),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb == "geocode":
            return await self._geocode(args.get("place", ""), int(args.get("limit", 5)))
        if verb == "directions":
            return await self._directions(
                args.get("from_place", ""),
                args.get("to_place", ""),
                args.get("mode", "car"),
            )
        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    async def _geocode(self, place: str, limit: int) -> ToolResult:
        if not place.strip():
            return ToolResult(success=False, error="place required")
        try:
            results = await _geocode(place, limit=limit)
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=str(e))
        return ToolResult(success=True, data={
            "results": [
                {
                    "display_name": r.get("display_name"),
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "type": r.get("type"),
                }
                for r in results
            ],
        })

    async def _directions(self, frm: str, to: str, mode: str) -> ToolResult:
        if mode not in {"car", "bike", "foot"}:
            mode = "car"
        profile = {"car": "driving", "bike": "cycling", "foot": "walking"}[mode]

        try:
            f = await _geocode(frm, limit=1)
            t = await _geocode(to, limit=1)
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"geocode failed: {e}")
        if not f or not t:
            return ToolResult(success=False, error="could not geocode endpoints")

        coords = f"{f[0]['lon']},{f[0]['lat']};{t[0]['lon']},{t[0]['lat']}"
        try:
            async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": USER_AGENT}) as client:
                resp = await client.get(f"{OSRM_URL}/{profile}/{coords}", params={"overview": "false"})
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=str(e))

        routes = data.get("routes") or []
        if not routes:
            return ToolResult(success=False, error="no route found")
        r = routes[0]
        return ToolResult(success=True, data={
            "from": f[0]["display_name"],
            "to": t[0]["display_name"],
            "distance_km": round(r["distance"] / 1000.0, 2),
            "duration_min": round(r["duration"] / 60.0, 1),
            "mode": mode,
        })
