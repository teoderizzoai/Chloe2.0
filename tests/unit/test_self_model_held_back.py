import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set, get as kv_get
from chloe.identity.self_model import _load_held_back_summary

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_held_back_memory(db, content, tool, days_ago=1):
    import ulid
    action_id = str(ulid.ULID())
    created_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()

    db.execute(
        """
        INSERT INTO actions (id, tool, verb, args, intent, state, authorization, preview, proposed_at)
        VALUES (?, ?, 'send_text', '{}', 'test', 'held_back', 'kinetic', 'preview', ?)
        """,
        (action_id, tool, created_at),
    )
    db.execute(
        """
        INSERT INTO memories (kind, text, source, source_ref, tags, artifact_refs, weight,
                              salience, confidence, archived_tier, created_at, updated_at)
        VALUES ('episodic', ?, 'gate', ?, ?, '[]', 0.5, 0.5, 1.0, 'hot', ?, ?)
        """,
        (content, action_id, json.dumps(["held_back", tool]), created_at, created_at),
    )
    db.commit()


def test_load_held_back_summary_counts(db):
    for i in range(4):
        _insert_held_back_memory(db, f"held back #{i}", "messages", days_ago=1)

    summary = _load_held_back_summary()
    assert summary["count_7d"] == 4


def test_load_held_back_top_tools(db):
    for _ in range(3):
        _insert_held_back_memory(db, "held", "messages", days_ago=1)
    _insert_held_back_memory(db, "held", "calendar", days_ago=1)

    summary = _load_held_back_summary()
    assert summary["top_tools"][0] == "messages"


def test_voice_drift_note_stored_in_kv(db):
    notes = [{"note": "I was quieter than usual.", "written_at": "2026-05-01T03:00:00"}]
    kv_set("voice_drift_notes", notes)

    stored = kv_get("voice_drift_notes")
    assert len(stored) == 1
    assert stored[0]["note"] == "I was quieter than usual."


def test_voice_drift_notes_cap_at_three(db):
    notes = [{"note": f"note {i}", "written_at": "..."} for i in range(4)]
    trimmed = notes[-3:]
    kv_set("voice_drift_notes", trimmed)
    stored = kv_get("voice_drift_notes")
    assert len(stored) == 3
    assert stored[-1]["note"] == "note 3"
