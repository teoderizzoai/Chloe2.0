import pytest
from unittest.mock import AsyncMock, patch

from chloe.tools.calendar import CalendarTool

MOCK_EVENTS = [
    {
        "id": "event1",
        "summary": "Dentist",
        "start": {"dateTime": "2026-05-04T09:00:00+01:00"},
        "end": {"dateTime": "2026-05-04T10:00:00+01:00"},
        "location": "Via Roma 1",
    },
    {
        "id": "event2",
        "summary": "Team standup",
        "start": {"dateTime": "2026-05-04T10:30:00+01:00"},
        "end": {"dateTime": "2026-05-04T11:00:00+01:00"},
    },
]


@pytest.fixture
def tool():
    return CalendarTool()


@pytest.mark.asyncio
async def test_read_today_returns_events(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    with patch.object(tool, "_get_events", AsyncMock(return_value=MOCK_EVENTS)):
        result = await tool.execute("read_today", {})
    assert result.success
    assert len(result.data["events"]) == 2
    assert result.data["events"][0]["title"] == "Dentist"
    assert result.data["events"][0]["location"] == "Via Roma 1"


@pytest.mark.asyncio
async def test_read_today_event_schema(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    with patch.object(tool, "_get_events", AsyncMock(return_value=MOCK_EVENTS)):
        result = await tool.execute("read_today", {})
    event = result.data["events"][0]
    assert {"title", "start", "end"}.issubset(event.keys())


@pytest.mark.asyncio
async def test_find_free_slot_with_no_events(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    with patch.object(tool, "_get_events", AsyncMock(return_value=[])):
        result = await tool.execute("find_free_slot", {
            "duration_minutes": 30,
            "between": {
                "start": "2026-05-04T08:00:00+00:00",
                "end": "2026-05-04T18:00:00+00:00",
            },
        })
    assert result.success
    assert result.data.get("slot_start") is not None


@pytest.mark.asyncio
async def test_find_free_slot_no_space(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    all_day = [{
        "id": "ev",
        "summary": "Busy",
        "start": {"dateTime": "2026-05-04T08:00:00+00:00"},
        "end": {"dateTime": "2026-05-04T18:00:00+00:00"},
    }]
    with patch.object(tool, "_get_events", AsyncMock(return_value=all_day)):
        result = await tool.execute("find_free_slot", {
            "duration_minutes": 30,
            "between": {
                "start": "2026-05-04T08:00:00+00:00",
                "end": "2026-05-04T18:00:00+00:00",
            },
        })
    assert result.success
    assert result.data.get("slot_start") is None


def test_read_verbs_are_intimate(tool):
    for verb in tool.verbs.values():
        if verb.reversibility == 1.0:
            assert verb.auth_class in ("intimate", "free"), f"{verb.name} should be read-only"
