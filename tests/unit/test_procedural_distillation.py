import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection
from chloe.memory.procedural import distill_procedural, _load_feedback_pairs

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_action(db, tool, verb, state, days_ago=1):
    import ulid
    action_id = str(ulid.ULID())
    proposed_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    db.execute(
        """
        INSERT INTO actions (id, tool, verb, args, intent, state, authorization, preview, proposed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (action_id, tool, verb, "{}", f"Test {verb}", state, "kinetic", "preview", proposed_at),
    )
    db.commit()
    return action_id


def test_load_feedback_pairs_includes_reverted(db):
    for _ in range(5):
        _insert_action(db, "calendar", "add_reminder", "reverted", days_ago=2)

    pairs = _load_feedback_pairs()
    calendar_pairs = [p for p in pairs if p["tool"] == "calendar"]
    assert len(calendar_pairs) == 5
    assert all(p["response_kind"] == "revert" for p in calendar_pairs)


def test_load_feedback_pairs_excludes_old_actions(db):
    _insert_action(db, "notes", "append", "reverted", days_ago=10)
    _insert_action(db, "notes", "append", "reverted", days_ago=3)

    pairs = _load_feedback_pairs()
    notes_pairs = [p for p in pairs if p["tool"] == "notes"]
    assert len(notes_pairs) == 1


@pytest.mark.asyncio
async def test_distill_procedural_creates_memories(db):
    for _ in range(5):
        _insert_action(db, "calendar", "add_reminder", "reverted", days_ago=1)

    mock_rule = {
        "rule_text": "When adding calendar reminders, avoid times before 9am.",
        "tool": "calendar",
        "tags": ["timing"],
        "confidence": 0.8,
        "valence": "avoid",
    }

    with patch("chloe.memory.procedural._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value=[mock_rule])
        with patch("chloe.memory.store.add_to_chroma"):
            result = await distill_procedural()

    assert len(result) >= 1


@pytest.mark.asyncio
async def test_distill_procedural_at_least_one_calendar_rule(db):
    for _ in range(5):
        _insert_action(db, "calendar", "add_reminder", "reverted", days_ago=1)

    with patch("chloe.memory.procedural._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value=[{
            "rule_text": "Avoid adding calendar reminders in early morning.",
            "tool": "calendar",
            "tags": [],
            "confidence": 0.75,
            "valence": "avoid",
        }])
        with patch("chloe.memory.store.add_to_chroma"):
            ids = await distill_procedural()

    assert len(ids) >= 1
    conn = get_connection()
    row = conn.execute(
        "SELECT tags FROM memories WHERE id = ?", (ids[0],)
    ).fetchone()
    assert row is not None
    tags = json.loads(row["tags"])
    assert "calendar" in tags


@pytest.mark.asyncio
async def test_distill_procedural_empty_when_no_pairs(db):
    result = await distill_procedural()
    assert result == []


@pytest.mark.asyncio
async def test_flash_error_does_not_crash(db):
    _insert_action(db, "notes", "append", "reverted", days_ago=1)

    with patch("chloe.memory.procedural._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(side_effect=Exception("LLM error"))
        result = await distill_procedural()
    assert result == []
