# X-01 · `tools/weather.py` — weather tool

## Overview

`WeatherTool` with two verbs: `current(lat, lon)` returns current weather conditions; `forecast(lat, lon, days)` returns a multi-day forecast. Backed by Open-Meteo (no API key required). Auth: `free`. Used by the initiative engine's opportunity vector to inform context-aware actions.

## Context

Weather is a `free`-tier tool — Chloe can call it without confirmation or leash checks. It has no side effects and costs nothing. Open-Meteo is an open-source weather API with no rate limits for reasonable usage. The tool is used by the initiative engine (`D-07`) to populate the opportunity vector and by the chat model to answer weather questions.

**When:** Phase B (needed by opportunity vector in D-07).

## Implementation

### `tools/weather.py`

```python
# chloe/tools/weather.py
from __future__ import annotations
import httpx
from chloe.tools.base import BaseTool, ToolVerb, ToolResult
from chloe.observability.logging import get_logger

log = get_logger("weather")

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Rime fog", 51: "Light drizzle", 53: "Drizzle",
    55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Light snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Heavy thunderstorm",
}


class WeatherTool(BaseTool):
    name = "weather"

    def __init__(self):
        self.verbs = {
            "current": ToolVerb(
                name="current",
                schema={
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "description": "Latitude"},
                        "lon": {"type": "number", "description": "Longitude"},
                    },
                    "required": ["lat", "lon"],
                },
                auth_class="free",
                reversibility=1.0,
                description_for_model="Get current weather conditions at a location (temperature, conditions, wind).",
                description_for_human="Current weather",
            ),
            "forecast": ToolVerb(
                name="forecast",
                schema={
                    "type": "object",
                    "properties": {
                        "lat": {"type": "number", "description": "Latitude"},
                        "lon": {"type": "number", "description": "Longitude"},
                        "days": {"type": "integer", "minimum": 1, "maximum": 7,
                                 "description": "Number of forecast days (1-7)"},
                    },
                    "required": ["lat", "lon"],
                },
                auth_class="free",
                reversibility=1.0,
                description_for_model="Get a multi-day weather forecast for a location.",
                description_for_human="Weather forecast",
            ),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        lat = args.get("lat")
        lon = args.get("lon")
        if lat is None or lon is None:
            return ToolResult(success=False, error="lat and lon required")

        if verb == "current":
            return await self._current(lat, lon)
        elif verb == "forecast":
            days = int(args.get("days", 3))
            return await self._forecast(lat, lon, days)
        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    async def _current(self, lat: float, lon: float) -> ToolResult:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": [
                "temperature_2m", "apparent_temperature", "weather_code",
                "wind_speed_10m", "wind_direction_10m", "relative_humidity_2m",
                "precipitation",
            ],
            "wind_speed_unit": "kmh",
            "timezone": "auto",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            current = data.get("current", {})
            code = current.get("weather_code", 0)
            return ToolResult(
                success=True,
                data={
                    "temperature_c": current.get("temperature_2m"),
                    "feels_like_c": current.get("apparent_temperature"),
                    "conditions": WMO_CODES.get(code, "Unknown"),
                    "weather_code": code,
                    "wind_kmh": current.get("wind_speed_10m"),
                    "wind_direction": current.get("wind_direction_10m"),
                    "humidity_pct": current.get("relative_humidity_2m"),
                    "precipitation_mm": current.get("precipitation"),
                    "timezone": data.get("timezone"),
                },
            )
        except httpx.HTTPStatusError as e:
            return ToolResult(success=False, error=f"Weather API error: {e.response.status_code}")
        except Exception as e:
            log.warning("weather_current_error", error=str(e))
            return ToolResult(success=False, error=str(e))

    async def _forecast(self, lat: float, lon: float, days: int) -> ToolResult:
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": [
                "weather_code", "temperature_2m_max", "temperature_2m_min",
                "precipitation_sum", "wind_speed_10m_max",
            ],
            "forecast_days": days,
            "timezone": "auto",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(OPEN_METEO_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

            daily = data.get("daily", {})
            dates = daily.get("time", [])
            forecasts = []
            for i, date in enumerate(dates):
                code = daily.get("weather_code", [])[i] if i < len(daily.get("weather_code", [])) else 0
                forecasts.append({
                    "date": date,
                    "conditions": WMO_CODES.get(code, "Unknown"),
                    "temp_max_c": daily.get("temperature_2m_max", [])[i] if i < len(daily.get("temperature_2m_max", [])) else None,
                    "temp_min_c": daily.get("temperature_2m_min", [])[i] if i < len(daily.get("temperature_2m_min", [])) else None,
                    "precipitation_mm": daily.get("precipitation_sum", [])[i] if i < len(daily.get("precipitation_sum", [])) else None,
                    "max_wind_kmh": daily.get("wind_speed_10m_max", [])[i] if i < len(daily.get("wind_speed_10m_max", [])) else None,
                })

            return ToolResult(
                success=True,
                data={"timezone": data.get("timezone"), "forecast": forecasts},
            )
        except Exception as e:
            log.warning("weather_forecast_error", error=str(e))
            return ToolResult(success=False, error=str(e))

    def dry_run(self, verb: str, args: dict) -> str:
        lat, lon = args.get("lat", "?"), args.get("lon", "?")
        if verb == "current":
            return f"Would fetch current weather at ({lat}, {lon})"
        elif verb == "forecast":
            return f"Would fetch {args.get('days', 3)}-day forecast at ({lat}, {lon})"
        return super().dry_run(verb, args)
```

### Default coordinates in config

```python
# In chloe/config.py:
home_lat: float = 45.4654  # Default: Milan
home_lon: float = 9.1859
```

### Register in ToolRegistry

```python
from chloe.tools.weather import WeatherTool
self._tools["weather"] = WeatherTool()
```

## Testing

### Unit tests — `tests/unit/test_weather.py`

```python
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from chloe.tools.weather import WeatherTool


@pytest.fixture
def tool():
    return WeatherTool()


def test_verbs_are_free_auth(tool):
    assert tool.verbs["current"].auth_class == "free"
    assert tool.verbs["forecast"].auth_class == "free"


def test_dry_run_current(tool):
    result = tool.dry_run("current", {"lat": 45.4, "lon": 9.1})
    assert "current" in result.lower() and "45.4" in result


def test_dry_run_forecast(tool):
    result = tool.dry_run("forecast", {"lat": 45.4, "lon": 9.1, "days": 5})
    assert "5" in result and "forecast" in result.lower()


@pytest.mark.asyncio
async def test_current_success(tool):
    mock_response = {
        "timezone": "Europe/Rome",
        "current": {
            "temperature_2m": 18.5,
            "apparent_temperature": 17.0,
            "weather_code": 1,
            "wind_speed_10m": 12.3,
            "wind_direction_10m": 270,
            "relative_humidity_2m": 65,
            "precipitation": 0.0,
        },
    }

    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=MagicMock(
                status_code=200,
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )
        )
        result = await tool.execute("current", {"lat": 45.4654, "lon": 9.1859})

    assert result.success
    assert result.data["temperature_c"] == 18.5
    assert result.data["conditions"] == "Mainly clear"
    assert result.data["timezone"] == "Europe/Rome"


@pytest.mark.asyncio
async def test_forecast_success(tool):
    mock_response = {
        "timezone": "Europe/Rome",
        "daily": {
            "time": ["2026-05-04", "2026-05-05", "2026-05-06"],
            "weather_code": [1, 61, 3],
            "temperature_2m_max": [22.0, 18.0, 20.0],
            "temperature_2m_min": [14.0, 12.0, 13.0],
            "precipitation_sum": [0.0, 8.5, 0.0],
            "wind_speed_10m_max": [15.0, 25.0, 10.0],
        },
    }

    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=MagicMock(
                status_code=200,
                json=lambda: mock_response,
                raise_for_status=lambda: None,
            )
        )
        result = await tool.execute("forecast", {"lat": 45.0, "lon": 9.0, "days": 3})

    assert result.success
    assert len(result.data["forecast"]) == 3
    assert result.data["forecast"][1]["conditions"] == "Light rain"


@pytest.mark.asyncio
async def test_missing_lat_lon_error(tool):
    result = await tool.execute("current", {})
    assert not result.success
    assert "lat" in result.error


@pytest.mark.asyncio
async def test_api_error_handled(tool):
    import httpx
    with patch("httpx.AsyncClient") as mock:
        mock.return_value.__aenter__.return_value.get = AsyncMock(
            side_effect=httpx.ConnectError("unreachable")
        )
        result = await tool.execute("current", {"lat": 45.0, "lon": 9.0})
    assert not result.success
```

## Dependencies

- D-07 (`initiative/opportunity.py` — consumes weather in OpportunityVector).
- `config.py` — `home_lat`, `home_lon`.
- Open-Meteo API (no auth, no rate limit).

## Acceptance criteria

- `current` and `forecast` have `auth_class="free"`.
- Successful API call → `ToolResult(success=True)` with weather data.
- Missing lat/lon → error without exception.
- HTTP error → error without exception.
- WMO weather codes mapped to human-readable strings.
- No API key required.
