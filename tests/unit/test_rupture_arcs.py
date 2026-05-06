"""E-12: rupture arc lifecycle — open, repair, fade."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chloe.state.db import migrate, close, get_connection
from chloe.affect.arc import (
    open_rupture, active_rupture, record_positive_turn,
    fade_stale, should_deliberate_all_kinetic,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


def test_open_rupture_creates_arc():
    arc_id = open_rupture(intensity=0.5, note="disagreement about boundaries")
    assert arc_id > 0

    arc = active_rupture()
    assert arc is not None
    assert arc["kind"] == "rupture"
    assert arc["state"] == "active"
    assert arc["intensity"] == pytest.approx(0.5)
    assert arc["note"] == "disagreement about boundaries"


def test_active_rupture_none_when_no_arc():
    assert active_rupture() is None


def test_should_deliberate_all_kinetic_with_active_rupture():
    open_rupture()
    assert should_deliberate_all_kinetic() is True


def test_should_deliberate_false_without_rupture():
    assert should_deliberate_all_kinetic() is False


def test_three_positive_turns_resolve_arc():
    arc_id = open_rupture()

    resolved = record_positive_turn(arc_id)
    assert not resolved
    resolved = record_positive_turn(arc_id)
    assert not resolved
    resolved = record_positive_turn(arc_id)
    assert resolved

    assert active_rupture() is None

    conn = get_connection()
    row = conn.execute("SELECT state FROM arcs WHERE id = ?", (arc_id,)).fetchone()
    assert row["state"] == "resolved"


def test_resolve_writes_autobiographical_memory():
    arc_id = open_rupture()
    for _ in range(3):
        record_positive_turn(arc_id)

    conn = get_connection()
    row = conn.execute(
        "SELECT text, tags FROM memories WHERE kind = 'autobiographical' AND source = 'arc'"
    ).fetchone()
    assert row is not None
    assert "repair" in row["text"].lower() or "back" in row["text"].lower()


def test_stale_arc_fades():
    conn = get_connection()
    old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    cursor = conn.execute(
        "INSERT INTO arcs (kind, intensity, state, started_at) VALUES ('rupture', 0.3, 'active', ?)",
        (old_time,),
    )
    conn.commit()
    arc_id = cursor.lastrowid

    faded = fade_stale()
    assert arc_id in faded

    row = conn.execute("SELECT state FROM arcs WHERE id = ?", (arc_id,)).fetchone()
    assert row["state"] == "faded"


def test_fade_writes_different_autobiographical_memory():
    conn = get_connection()
    old_time = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    conn.execute(
        "INSERT INTO arcs (kind, intensity, state, started_at) VALUES ('rupture', 0.3, 'active', ?)",
        (old_time,),
    )
    conn.commit()

    fade_stale()

    row = conn.execute(
        "SELECT text, tags FROM memories WHERE kind = 'autobiographical' AND source = 'arc'"
    ).fetchone()
    assert row is not None
    assert "faded" in row["text"].lower() or "unspoken" in row["text"].lower()


def test_active_rupture_raises_deliberate_flag():
    open_rupture()
    from chloe.affect.arc import should_deliberate_all_kinetic
    assert should_deliberate_all_kinetic() is True
