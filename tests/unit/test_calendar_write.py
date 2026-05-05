import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from chloe.tools.calendar import CalendarTool


@pytest.fixture
def tool():
    return CalendarTool()


def test_add_event_dry_run(tool):
    preview = tool.dry_run("add_event", {
        "title": "Team meeting",
        "start": "2026-05-05T10:00:00",
        "end": "2026-05-05T11:00:00",
    })
    assert "Team meeting" in preview
    assert "2026-05-05" in preview


def test_add_reminder_dry_run(tool):
    preview = tool.dry_run("add_reminder", {"time": "2026-05-05T09:00:00", "body": "Call dentist"})
    assert "Call dentist" in preview


def test_decline_dry_run(tool):
    preview = tool.dry_run("decline", {"eventId": "ev_abc"})
    assert "ev_abc" in preview


def test_decline_is_kinetic_sensitive(tool):
    assert tool.verbs["decline"].auth_class == "kinetic-sensitive"


def test_add_event_is_kinetic(tool):
    assert tool.verbs["add_event"].auth_class == "kinetic"


def test_delete_event_is_reverse_of_add_event(tool):
    assert tool.verbs["add_event"].reverse_verb == "delete_event"


@pytest.mark.asyncio
async def test_add_event_success(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})

    async def mock_post(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "event_xyz", "summary": "Team meeting"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        with patch("chloe.tools.calendar._register_artifact"):
            result = await tool.execute("add_event", {
                "title": "Team meeting",
                "start": "2026-05-05T10:00:00+01:00",
                "end": "2026-05-05T11:00:00+01:00",
            })

    assert result.success
    assert result.data["eventId"] == "event_xyz"


@pytest.mark.asyncio
async def test_add_event_artifact_row(tool, tmp_path, monkeypatch):
    from pathlib import Path
    from chloe.state.db import migrate, close, get_connection
    MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)

    monkeypatch.setattr("chloe.tools.calendar.load_token", lambda s: {"access_token": "tok"})

    async def mock_post(url, headers=None, json=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"id": "ev_abc"}
        return resp

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = mock_post
        result = await tool.execute("add_event", {
            "title": "Dentist", "start": "2026-05-05T09:00:00+01:00",
            "end": "2026-05-05T10:00:00+01:00",
        })

    assert result.success
    conn = get_connection()
    row = conn.execute("SELECT * FROM artifact_index WHERE ref='ev_abc'").fetchone()
    assert row is not None
    assert row["kind"] == "calendar_event"
    close()
