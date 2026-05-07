import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set, get as kv_get
from chloe.initiative.curiosity import (
    generate_curiosity_candidates,
    mark_curiosity_surfaced,
    _load_cooled_topics,
    _days_since,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_person(db, name="Teo", depth=0.9):
    db.execute(
        "INSERT INTO persons (name, is_active, attachment_depth) VALUES (?, 1, ?)",
        (name, depth),
    )
    db.commit()
    return db.execute("SELECT id FROM persons WHERE name=?", (name,)).fetchone()["id"]


def _insert_memory_with_topic(db, person_id, topic, days_ago=25):
    ts = (datetime.utcnow() - timedelta(days=days_ago)).isoformat()
    db.execute(
        "INSERT INTO memories (kind, text, source, weight, tags, created_at)"
        " VALUES ('episodic', 'memory about topic', 'chat', 0.5, ?, ?)",
        (json.dumps([f"topic:{topic}"]), ts),
    )
    db.commit()


def test_stale_topic_generates_candidate(db):
    pid = _insert_person(db)
    _insert_memory_with_topic(db, pid, "sister", days_ago=25)
    candidates = generate_curiosity_candidates()
    assert len(candidates) > 0
    assert any("sister" in c.topic for c in candidates)


def test_recent_topic_no_candidate(db):
    pid = _insert_person(db)
    _insert_memory_with_topic(db, pid, "work", days_ago=1)
    candidates = generate_curiosity_candidates()
    assert not any("work" in c.topic for c in candidates)


def test_cooled_topic_excluded(db):
    pid = _insert_person(db)
    _insert_memory_with_topic(db, pid, "sister", days_ago=30)
    mark_curiosity_surfaced("sister")
    candidates = generate_curiosity_candidates()
    assert not any("sister" in c.topic for c in candidates)


def test_mark_surfaced_writes_kv(db):
    mark_curiosity_surfaced("work")
    cooled = _load_cooled_topics()
    assert "work" in cooled


def test_candidates_capped_at_two(db):
    pid = _insert_person(db)
    for topic in ["sister", "work", "project", "health", "travel"]:
        _insert_memory_with_topic(db, pid, topic, days_ago=30)
    candidates = generate_curiosity_candidates()
    assert len(candidates) <= 2


def test_days_since_calculation():
    past = (datetime.utcnow() - timedelta(days=10)).isoformat()
    result = _days_since(past, datetime.utcnow())
    assert 9.9 < result < 10.1


def test_days_since_invalid_date():
    result = _days_since("not-a-date", datetime.utcnow())
    assert result is None


def test_pressure_bounded(db):
    pid = _insert_person(db)
    _insert_memory_with_topic(db, pid, "sister", days_ago=90)
    candidates = generate_curiosity_candidates()
    for c in candidates:
        assert c.pressure <= 0.45
