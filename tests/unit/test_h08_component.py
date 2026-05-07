import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_reverted_calendar(db, days_ago=1):
    import ulid
    action_id = str(ulid.ULID())
    proposed_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    db.execute(
        """
        INSERT INTO actions (id, tool, verb, args, intent, state, authorization, preview, proposed_at)
        VALUES (?, 'calendar', 'add_reminder', ?, ?, 'reverted', 'kinetic', 'Add standup reminder', ?)
        """,
        (action_id, json.dumps({"title": "Standup"}), "Add standup", proposed_at),
    )
    db.commit()
    return action_id


@pytest.mark.asyncio
async def test_three_reverts_produce_calendar_rule(db):
    for i in range(3):
        _insert_reverted_calendar(db, days_ago=i + 1)

    mock_rule = {
        "rule_text": "Avoid adding calendar reminders before 9am — Teo has reverted these repeatedly.",
        "tool": "calendar",
        "tags": ["timing", "morning"],
        "confidence": 0.8,
        "valence": "avoid",
    }

    with patch("chloe.memory.procedural._gemini") as mock_gemini:
        mock_gemini.flash = AsyncMock(return_value=[mock_rule])
        with patch("chloe.memory.store.add_to_chroma"):
            from chloe.memory.procedural import distill_procedural
            ids = await distill_procedural()

    assert len(ids) >= 1
    conn = get_connection()
    row = conn.execute("SELECT tags FROM memories WHERE id = ?", (ids[0],)).fetchone()
    tags = json.loads(row["tags"])
    assert "calendar" in tags


@pytest.mark.asyncio
async def test_procedural_rule_retrieved_in_deliberation(db):
    conn = db
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO memories (kind, text, source, tags, artifact_refs, weight, salience,
                              confidence, archived_tier, created_at, updated_at)
        VALUES ('procedural', ?, 'distillation', ?, '[]', 0.8, 0.5, 1.0, 'hot', ?, ?)
        """,
        (
            "Avoid adding calendar reminders before 9am.",
            json.dumps(["procedural", "calendar"]),
            now, now,
        ),
    )
    conn.commit()

    class MockAction:
        id = "test"
        tool = "calendar"
        verb = "add_reminder"
        args = {}
        intent = "Add reminder"
        authorization = "kinetic"
        preview = "Add reminder"
        cost_estimate = None

    from unittest.mock import MagicMock

    mock_col = MagicMock()
    mock_col.query.return_value = {
        "ids": [["1"]],
        "metadatas": [[{"kind": "procedural", "source": "distillation", "has_artifact": 0}]],
        "distances": [[0.1]],
    }
    mock_col.count.return_value = 1

    with patch("chloe.state.chroma.get_collection", return_value=mock_col):
        from chloe.actions.deliberate import _get_procedural_memories
        hits = _get_procedural_memories(MockAction())

    assert len(hits) >= 1
