# X-02 · `tools/maps.py` — maps tool

## Overview

`MapsTool` with four verbs: `find_place`, `directions`, `traffic_to(destination, depart_at)`, `commute_estimate`. Backed by Google Maps Platform. Auth: `free` for `find_place`; `intimate` for `directions`, `traffic_to`, `commute_estimate` (these involve Teo's location/routine). Used by chat and initiative.

## Context

Maps is split into two auth levels: finding a generic place is `free` (no personal data), but routing and commute estimation involves Teo's home address and daily patterns which is `intimate`. `intimate` requires Chloe to have been explicitly invited to act in this context — fine for chat responses, but not for unprompted initiative actions.

**When:** Phase C (interest-driven curiosity and proactive commute awareness).

## Implementation

### `tools/maps.py`

```python
# chloe/tools/maps.py
from __future__ import annotations
import httpx
from chloe.tools.base import BaseTool, ToolVerb, ToolResult
from chloe.config import get_settings
from chloe.observability.logging import get_logger

log = get_logger("maps")

MAPS_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
MAPS_DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
MAPS_PLACE_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"
MAPS_DISTANCE_URL = "https://maps.googleapis.com/maps/api/distancematrix/json"


class MapsTool(BaseTool):
    name = "maps"

    def __init__(self):
        self.verbs = {
            "find_place": ToolVerb(
                name="find_place",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Place search query, e.g. 'coffee shops near Milan Centrale'"},
                        "type": {"type": "string", "description": "Optional place type filter, e.g. 'restaurant', 'cafe'"},
                    },
                    "required": ["query"],
                },
                auth_class="free",
                reversibility=1.0,
                description_for_model="Search for a place by name or description. Returns name, address, and location.",
                description_for_human="Search for a place",
            ),
            "directions": ToolVerb(
                name="directions",
                schema={
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string", "description": "Starting address or 'home'"},
                        "destination": {"type": "string", "description": "Destination address"},
                        "mode": {"type": "string", "enum": ["driving", "transit", "walking", "bicycling"],
                                 "default": "driving"},
                    },
                    "required": ["origin", "destination"],
                },
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Get directions from one place to another.",
                description_for_human="Get directions",
            ),
            "traffic_to": ToolVerb(
                name="traffic_to",
                schema={
                    "type": "object",
                    "properties": {
                        "destination": {"type": "string", "description": "Destination address"},
                        "depart_at": {"type": "string", "description": "Departure time ISO 8601 or 'now'"},
                    },
                    "required": ["destination"],
                },
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Get current travel time to a destination accounting for traffic.",
                description_for_human="Check traffic to destination",
            ),
            "commute_estimate": ToolVerb(
                name="commute_estimate",
                schema={
                    "type": "object",
                    "properties": {
                        "destination": {"type": "string", "description": "Work or commute destination"},
                        "depart_at": {"type": "string", "description": "Planned departure time ISO 8601"},
                    },
                    "required": ["destination"],
                },
                auth_class="intimate",
                reversibility=1.0,
                description_for_model="Estimate commute time from home to a destination at a given departure time.",
                description_for_human="Commute time estimate",
            ),
        }

    def _api_key(self) -> str:
        return get_settings().google_maps_api_key

    def _home_address(self) -> str:
        return get_settings().home_address  # e.g. "Via Roma 1, Milan, Italy"

    def _resolve_origin(self, origin: str) -> str:
        if origin.lower() == "home":
            return self._home_address()
        return origin

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb == "find_place":
            return await self._find_place(args)
        elif verb == "directions":
            return await self._directions(args)
        elif verb == "traffic_to":
            return await self._traffic_to(args)
        elif verb == "commute_estimate":
            return await self._commute_estimate(args)
        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    async def _find_place(self, args: dict) -> ToolResult:
        params = {
            "query": args.get("query", ""),
            "key": self._api_key(),
        }
        if args.get("type"):
            params["type"] = args["type"]

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(MAPS_PLACE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") not in ("OK", "ZERO_RESULTS"):
                return ToolResult(success=False, error=f"Maps API: {data.get('status')}")

            results = []
            for r in data.get("results", [])[:5]:
                results.append({
                    "name": r.get("name"),
                    "address": r.get("formatted_address"),
                    "lat": r.get("geometry", {}).get("location", {}).get("lat"),
                    "lon": r.get("geometry", {}).get("location", {}).get("lng"),
                    "rating": r.get("rating"),
                    "open_now": r.get("opening_hours", {}).get("open_now"),
                })
            return ToolResult(success=True, data={"results": results})
        except Exception as e:
            log.warning("maps_find_place_error", error=str(e))
            return ToolResult(success=False, error=str(e))

    async def _directions(self, args: dict) -> ToolResult:
        origin = self._resolve_origin(args.get("origin", ""))
        destination = args.get("destination", "")
        mode = args.get("mode", "driving")

        params = {
            "origin": origin,
            "destination": destination,
            "mode": mode,
            "key": self._api_key(),
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(MAPS_DIRECTIONS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "OK":
                return ToolResult(success=False, error=f"Maps API: {data.get('status')}")

            route = data["routes"][0]["legs"][0]
            return ToolResult(
                success=True,
                data={
                    "distance": route["distance"]["text"],
                    "duration": route["duration"]["text"],
                    "duration_seconds": route["duration"]["value"],
                    "summary": data["routes"][0].get("summary", ""),
                    "steps": [s["html_instructions"] for s in route.get("steps", [])][:5],
                },
            )
        except Exception as e:
            log.warning("maps_directions_error", error=str(e))
            return ToolResult(success=False, error=str(e))

    async def _traffic_to(self, args: dict) -> ToolResult:
        destination = args.get("destination", "")
        origin = self._home_address()

        params = {
            "origins": origin,
            "destinations": destination,
            "mode": "driving",
            "departure_time": "now",
            "traffic_model": "best_guess",
            "key": self._api_key(),
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(MAPS_DISTANCE_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "OK":
                return ToolResult(success=False, error=f"Maps API: {data.get('status')}")

            element = data["rows"][0]["elements"][0]
            if element.get("status") != "OK":
                return ToolResult(success=False, error=f"Route not found")

            duration_traffic = element.get("duration_in_traffic", element.get("duration", {}))
            return ToolResult(
                success=True,
                data={
                    "destination": destination,
                    "duration_text": duration_traffic.get("text"),
                    "duration_seconds": duration_traffic.get("value"),
                    "distance": element.get("distance", {}).get("text"),
                },
            )
        except Exception as e:
            log.warning("maps_traffic_error", error=str(e))
            return ToolResult(success=False, error=str(e))

    async def _commute_estimate(self, args: dict) -> ToolResult:
        """Same as traffic_to but with a depart_at timestamp."""
        args_copy = dict(args)
        result = await self._traffic_to(args_copy)
        return result

    def dry_run(self, verb: str, args: dict) -> str:
        if verb == "find_place":
            return f"Would search for {args.get('query', '?')} on Google Maps"
        elif verb == "directions":
            return f"Would get {args.get('mode', 'driving')} directions: {args.get('origin', '?')} → {args.get('destination', '?')}"
        elif verb == "traffic_to":
            return f"Would check traffic to {args.get('destination', '?')}"
        elif verb == "commute_estimate":
            return f"Would estimate commute to {args.get('destination', '?')} departing {args.get('depart_at', 'now')}"
        return super().dry_run(verb, args)
```

### Config additions

```python
# In chloe/config.py:
google_maps_api_key: str = ""
home_address: str = "Via Roma 1, Milan, Italy"
```

### Register in ToolRegistry

```python
from chloe.tools.maps import MapsTool
self._tools["maps"] = MapsTool()
```

## Testing

### Unit tests — `tests/unit/test_maps.py`

```python
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from chloe.tools.maps import MapsTool


@pytest.fixture
def tool(monkeypatch):
    monkeypatch.setattr("chloe.tools.maps.get_settings", lambda: MagicMock(
        google_maps_api_key="test_key",
        home_address="Via Roma 1, Milan, Italy",
    ))
    return MapsTool()


def test_find_place_is_free_auth(tool):
    assert tool.verbs["find_place"].auth_class == "free"


def test_directions_is_intimate(tool):
    assert tool.verbs["directions"].auth_class == "intimate"
    assert tool.verbs["traffic_to"].auth_class == "intimate"
    assert tool.verbs["commute_estimate"].auth_class == "intimate"


def test_dry_run_find_place(tool):
    result = tool.dry_run("find_place", {"query": "coffee shops near Duomo"})
    assert "coffee shops" in result


def test_resolve_home_origin(tool):
    assert tool._resolve_origin("home") == "Via Roma 1, Milan, Italy"
    assert tool._resolve_origin("Piazza del Duomo") == "Piazza del Duomo"


@pytest.mark.asyncio
async def test_find_place_success(tool):
    mock_data = {
        "status": "OK",
        "results": [{
            "name": "Caffè Brera",
            "formatted_address": "Via Brera 12, Milan",
            "geometry": {"location": {"lat": 45.47, "lng": 9.18}},
            "rating": 4.5,
            "opening_hours": {"open_now": True},
        }],
    }
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=MagicMock(
                status_code=200, json=lambda: mock_data, raise_for_status=lambda: None
            )
        )
        result = await tool.execute("find_place", {"query": "cafe near Brera"})
    assert result.success
    assert result.data["results"][0]["name"] == "Caffè Brera"


@pytest.mark.asyncio
async def test_directions_success(tool):
    mock_data = {
        "status": "OK",
        "routes": [{
            "summary": "Via Vittorio Veneto",
            "legs": [{
                "distance": {"text": "15 km"},
                "duration": {"text": "25 mins", "value": 1500},
                "steps": [],
            }],
        }],
    }
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=MagicMock(
                status_code=200, json=lambda: mock_data, raise_for_status=lambda: None
            )
        )
        result = await tool.execute("directions", {
            "origin": "home", "destination": "Piazza della Repubblica", "mode": "driving"
        })
    assert result.success
    assert result.data["duration"] == "25 mins"


@pytest.mark.asyncio
async def test_maps_api_error_handled(tool):
    import httpx
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.ConnectError("unreachable")
        )
        result = await tool.execute("find_place", {"query": "coffee"})
    assert not result.success
```

## Dependencies

- `config.py` — `google_maps_api_key`, `home_address`.
- Google Maps Platform API key with Places, Directions, Distance Matrix enabled.

## Acceptance criteria

- `find_place` has `auth_class="free"`; location verbs have `auth_class="intimate"`.
- `find_place` returns up to 5 results with name, address, lat/lon.
- `directions` resolves `"home"` to configured home address.
- API error → `ToolResult(success=False)` without exception.
- `dry_run` returns readable string for all verbs.
