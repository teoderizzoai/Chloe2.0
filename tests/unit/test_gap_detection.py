import pytest
from pathlib import Path
from datetime import datetime, timedelta
from chloe.state.db import migrate, close, get_connection
from chloe.initiative.gaps import detect_gaps, _person_gaps, _belief_gaps

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def _insert_person(db, name="Teo"):
    db.execute(
        "INSERT INTO persons (name, is_active, attachment_depth) VALUES (?, 1, 0.9)",
        (name,),
    )
    db.commit()
    return db.execute("SELECT id FROM persons WHERE name=?", (name,)).fetchone()["id"]


def test_no_person_fields_yields_gaps(db):
    _insert_person(db, "Teo")
    gaps = _person_gaps()
    assert len(gaps) > 0
    assert all(g.kind == "person" for g in gaps)


def test_fresh_person_field_no_gap(db):
    pid = _insert_person(db)
    db.execute(
        "INSERT INTO person_fields (person_id, field_name, value, updated_at)"
        " VALUES (?, 'schedule', 'M-F 9-6', datetime('now'))",
        (pid,),
    )
    db.commit()
    gaps = _person_gaps()
    subjects = [g.subject for g in gaps]
    assert not any("schedule/routine" in s for s in subjects)


def test_stale_person_field_yields_gap(db):
    pid = _insert_person(db)
    old = (datetime.utcnow() - timedelta(days=40)).isoformat()
    db.execute(
        "INSERT INTO person_fields (person_id, field_name, value, updated_at)"
        " VALUES (?, 'schedule', 'old', ?)",
        (pid, old),
    )
    db.commit()
    gaps = _person_gaps()
    assert any("schedule" in g.subject for g in gaps)


def test_low_confidence_belief_yields_gap(db):
    old = (datetime.utcnow() - timedelta(days=20)).isoformat()
    db.execute(
        "INSERT INTO inner_beliefs (text, confidence, archived, updated_at)"
        " VALUES ('Teo prefers tea', 0.2, 0, ?)",
        (old,),
    )
    db.commit()
    gaps = _belief_gaps()
    assert len(gaps) == 1
    assert gaps[0].kind == "belief"


def test_detect_gaps_sorted_by_priority(db):
    _insert_person(db)
    gaps = detect_gaps()
    priorities = [g.priority for g in gaps]
    assert priorities == sorted(priorities, reverse=True)


def test_gap_candidates_capped_at_three(db):
    for i in range(10):
        _insert_person(db, f"Person{i}")
    from chloe.initiative.gaps import gap_driven_candidates
    candidates = gap_driven_candidates()
    assert len(candidates) <= 3
