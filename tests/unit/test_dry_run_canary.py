import pytest
from unittest.mock import MagicMock, patch

from chloe.tools.calendar import CalendarTool
from chloe.tools.gmail import GmailTool
from chloe.tools.reminders import RemindersTool
from chloe.tools.spotify import SpotifyTool

KINETIC_SCENARIOS = [
    (SpotifyTool, "queue_track", {"uri": "spotify:track:abc123"}),
    (SpotifyTool, "start_playlist", {"uri": "spotify:playlist:xyz"}),
    (SpotifyTool, "like", {"uri": "spotify:track:abc123"}),
    (SpotifyTool, "skip", {}),
    (CalendarTool, "add_event", {"title": "Meeting", "start": "2026-05-05T10:00:00", "end": "2026-05-05T11:00:00"}),
    (CalendarTool, "add_reminder", {"time": "2026-05-05T09:00:00", "body": "check email"}),
    (GmailTool, "draft_reply", {"threadId": "t1", "body": "hello"}),
    (RemindersTool, "add", {"title": "Buy milk"}),
    (RemindersTool, "complete", {"taskId": "task1"}),
]


@pytest.mark.parametrize("ToolClass,verb,args", KINETIC_SCENARIOS)
def test_dry_run_makes_no_http_call(ToolClass, verb, args):
    tool = ToolClass()
    http_called = []

    def spy(*a, **kw):
        http_called.append((a, kw))
        return MagicMock()

    with patch("httpx.AsyncClient", spy), patch("httpx.Client", spy):
        result = tool.dry_run(verb, args)

    assert len(http_called) == 0, f"{ToolClass.__name__}.dry_run('{verb}') made HTTP call(s)"
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("ToolClass,verb,args", KINETIC_SCENARIOS)
async def test_registry_dry_run_mode_no_http(ToolClass, verb, args, monkeypatch):
    from chloe.tools.registry import ToolRegistry

    monkeypatch.setattr("chloe.tools.registry.get_settings", lambda: MagicMock(dry_run=True))
    monkeypatch.setattr("chloe.tools.spotify.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.gmail.load_token", lambda s: {"access_token": "tok"})
    monkeypatch.setattr("chloe.tools.reminders.load_token", lambda s: {"access_token": "tok"})

    registry = ToolRegistry()
    registry.register(SpotifyTool())
    registry.register(CalendarTool())
    registry.register(GmailTool())
    registry.register(RemindersTool())

    http_called = []

    def spy(*a, **kw):
        http_called.append((a, kw))
        raise AssertionError("HTTP call made in dry-run mode")

    tool_name = ToolClass().name
    with patch("httpx.AsyncClient", spy):
        result = await registry.execute(tool_name, verb, args)

    assert result.is_dry_run is True
    assert result.success is True
    assert len(http_called) == 0


@pytest.mark.asyncio
async def test_intimate_reads_not_blocked_by_dry_run(monkeypatch):
    from chloe.tools.registry import ToolRegistry

    monkeypatch.setattr("chloe.tools.registry.get_settings", lambda: MagicMock(dry_run=True))

    registry = ToolRegistry()
    tool = SpotifyTool()
    registry.register(tool)

    result = await registry.execute("spotify", "show_currently_playing", {})
    assert result.is_dry_run is True
