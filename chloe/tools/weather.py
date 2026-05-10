"""Weather tool — Open-Meteo (no API key required).

Two verbs:
- `current(latitude, longitude)` or `current(place)` — current conditions
- `forecast(latitude, longitude, days)` — daily forecast up to 7 days
"""
from __future__ import annotations

import httpx

from chloe.observability.logging import get_logger
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.weather")

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

WMO_CODES = {
    0: "clear", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    77: "snow grains",
    80: "rain showers", 81: "heavy showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "severe thunderstorm with hail",
}


async def _geocode(place: str) -> tuple[float, float, str] | None:
    async with httpx.AsyncClient(timeout=8.0) as client:
        resp = await client.get(GEOCODE_URL, params={"name": place, "count": 1})
        resp.raise_for_status()
        data = resp.json()
    results = data.get("results") or []
    if not results:
        return None
    r = results[0]
    return r["latitude"], r["longitude"], r.get("name", place)


class WeatherTool(Tool):
    name = "weather"

    def __init__(self):
        self.verbs = {
            "current": ToolVerb(
                name="current",
                schema={
                    "type": "object",
                    "properties": {
                        "place": {"type": "string"},
                        "latitude": {"type": "number"},
                        "longitude": {"type": "number"},
                    },
                },
                auth_class="free",
                reversibility=1.0,
                cost_per_call_usd=0.0,
                description_for_model="Get current weather for a place name or lat/lon.",
                description_for_human="Current weather",
            ),
            "forecast": ToolVerb(
                name="forecast",
                schema={
                    "type": "object",
                    "properties": {
                        "place": {"type": "string"},
                        "latitude": {"type": "number"},
                        "longitude": {"type": "number"},
                        "days": {"type": "integer", "minimum": 1, "maximum": 7},
                    },
                },
                auth_class="free",
                reversibility=1.0,
                cost_per_call_usd=0.0,
                description_for_model="Get a daily forecast (1–7 days).",
                description_for_human="Weather forecast",
            ),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        try:
            lat, lon, label = await self._resolve(args)
        except ValueError as e:
            return ToolResult(success=False, error=str(e))
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=f"geocode failed: {e}")

        if verb == "current":
            return await self._current(lat, lon, label)
        if verb == "forecast":
            days = max(1, min(7, int(args.get("days", 3))))
            return await self._forecast(lat, lon, label, days)
        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    async def _resolve(self, args: dict) -> tuple[float, float, str]:
        if args.get("latitude") is not None and args.get("longitude") is not None:
            return float(args["latitude"]), float(args["longitude"]), args.get("place") or "lat/lon"
        place = (args.get("place") or "").strip()
        if not place:
            raise ValueError("place or latitude/longitude required")
        result = await _geocode(place)
        if not result:
            raise ValueError(f"could not geocode: {place!r}")
        return result

    async def _current(self, lat: float, lon: float, label: str) -> ToolResult:
        params = {
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
            "timezone": "auto",
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(FORECAST_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=str(e))

        c = data.get("current", {})
        return ToolResult(success=True, data={
            "place": label,
            "temperature_c": c.get("temperature_2m"),
            "feels_like_c": c.get("apparent_temperature"),
            "humidity": c.get("relative_humidity_2m"),
            "wind_kmh": c.get("wind_speed_10m"),
            "condition": WMO_CODES.get(c.get("weather_code"), "unknown"),
        })

    async def _forecast(self, lat: float, lon: float, label: str, days: int) -> ToolResult:
        params = {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
            "timezone": "auto",
            "forecast_days": days,
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(FORECAST_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as e:
            return ToolResult(success=False, error=str(e))

        d = data.get("daily", {})
        dates = d.get("time", [])
        out = []
        for i, date in enumerate(dates):
            out.append({
                "date": date,
                "high_c": (d.get("temperature_2m_max") or [None])[i],
                "low_c": (d.get("temperature_2m_min") or [None])[i],
                "precipitation_mm": (d.get("precipitation_sum") or [None])[i],
                "condition": WMO_CODES.get((d.get("weather_code") or [None])[i], "unknown"),
            })
        return ToolResult(success=True, data={"place": label, "days": out})
