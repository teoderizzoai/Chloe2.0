import pytest
from pathlib import Path
from datetime import datetime, timedelta
from chloe.state.db import migrate, close, get_connection
from chloe.identity.narrative import (
    append_narrative_entry,
    get_my_story,
    get_recent_chapter,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_append_and_retrieve_entry(db):
    entry_id = append_narrative_entry(
        kind="event",
        title="Goal completed",
        body="I finished the goal I'd been working on for two weeks.",
        valence=0.6,
        source="test",
    )
    row = db.execute("SELECT * FROM narrative_events WHERE id=?", (entry_id,)).fetchone()
    assert row is not None
    assert row["kind"] == "event"
    assert row["valence"] == pytest.approx(0.6)


def test_get_my_story_returns_entries_in_window(db):
    append_narrative_entry("chapter", "Week 1", "A quiet week.", source="weekly")
    story = get_my_story(window_days=30)
    assert "A quiet week." in story


def test_get_my_story_excludes_old_entries(db):
    old = (datetime.utcnow() - timedelta(days=40)).isoformat()
    conn = get_connection()
    from chloe.actions.schema import ulid as make_ulid
    conn.execute(
        "INSERT INTO narrative_events (id, kind, title, body, source, created_at)"
        " VALUES (?, 'event', 'Old event', 'This was a long time ago.', 'test', ?)",
        (make_ulid(), old),
    )
    conn.commit()
    story = get_my_story(window_days=30)
    assert "This was a long time ago." not in story


def test_get_my_story_empty_returns_placeholder(db):
    story = get_my_story(window_days=30)
    assert "Nothing significant" in story


def test_get_recent_chapter_returns_latest(db):
    append_narrative_entry("chapter", "Week 1", "First chapter body.", source="weekly")
    append_narrative_entry("chapter", "Week 2", "Second chapter body.", source="weekly")
    chapter = get_recent_chapter()
    assert "Second chapter body." in chapter


def test_get_recent_chapter_empty_returns_empty_string(db):
    chapter = get_recent_chapter()
    assert chapter == ""


def test_get_recent_chapter_respects_max_chars(db):
    long_body = "a" * 500
    append_narrative_entry("chapter", "Long week", long_body, source="weekly")
    chapter = get_recent_chapter(max_chars=50)
    assert len(chapter) <= 53
    assert chapter.endswith("…")


def test_multiple_kinds_all_appear_in_story(db):
    append_narrative_entry("event", "Goal done", "I finished a goal.", source="goal")
    append_narrative_entry("revision", "Belief updated", "I revised a belief.", source="belief_revision")
    append_narrative_entry("trait_shift", "Trait emerged", "A trait crystallised.", source="traits")
    story = get_my_story(window_days=30)
    assert "finished a goal" in story
    assert "revised a belief" in story
    assert "trait crystallised" in story
