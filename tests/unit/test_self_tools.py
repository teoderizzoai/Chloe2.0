import pytest
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.tools.self_tools import SelfToolsTool, _parse_until
from datetime import datetime

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


@pytest.fixture
def tool():
    return SelfToolsTool()


def test_all_verbs_are_free(tool):
    for verb in tool.verbs.values():
        assert verb.auth_class == "free", f"{verb.name} is not free"


def test_parse_until_iso():
    dt = _parse_until("2026-05-10T10:00:00")
    assert dt == datetime(2026, 5, 10, 10, 0, 0)


def test_parse_until_duration():
    before = datetime.utcnow()
    dt = _parse_until("2h")
    assert dt is not None
    diff = dt - before
    assert 7100 < diff.total_seconds() < 7300


def test_parse_until_minutes():
    dt = _parse_until("30m")
    assert dt is not None


def test_parse_until_invalid():
    assert _parse_until("garbage") is None
    assert _parse_until("") is None


@pytest.mark.asyncio
async def test_set_quiet_writes_preference(tool, db):
    result = await tool.execute("set_quiet", {"until": "2h"})
    assert result.success
    conn = get_connection()
    row = conn.execute("SELECT value FROM preferences WHERE key='quiet_until'").fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_set_focus_writes_preference(tool, db):
    import json
    result = await tool.execute("set_focus", {"mode": True})
    assert result.success
    conn = get_connection()
    row = conn.execute("SELECT value FROM preferences WHERE key='focus_mode'").fetchone()
    assert json.loads(row["value"]) is True


@pytest.mark.asyncio
async def test_add_goal_creates_row(tool, db):
    result = await tool.execute("add_goal", {"tag": "learn_python", "description": "Master Python async"})
    assert result.success
    goal_id = result.data["goal_id"]
    conn = get_connection()
    row = conn.execute("SELECT * FROM inner_goals WHERE id=?", (goal_id,)).fetchone()
    assert row["name"] == "learn_python"


@pytest.mark.asyncio
async def test_add_want_creates_row(tool, db):
    result = await tool.execute("add_want", {"description": "Understand music theory better"})
    assert result.success
    want_id = result.data["want_id"]
    conn = get_connection()
    row = conn.execute("SELECT * FROM inner_wants WHERE id=?", (want_id,)).fetchone()
    assert "music theory" in row["text"]


@pytest.mark.asyncio
async def test_update_preference_blocked_keys(tool, db):
    for key in ["ha_blocklist", "ha_allowlist", "gmail_dont_send_to"]:
        result = await tool.execute("update_preference", {"key": key, "value": []})
        assert not result.success
        assert "cannot be modified" in result.error


@pytest.mark.asyncio
async def test_update_preference_allowed_key(tool, db):
    result = await tool.execute("update_preference", {"key": "custom_note", "value": "hello"})
    assert result.success


@pytest.mark.asyncio
async def test_archive_trait_nonexistent(tool, db):
    result = await tool.execute("archive_trait", {"trait_id": "99999"})
    assert not result.success
    assert "not found" in result.error
