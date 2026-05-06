"""E-10: decay_all() applies half-life decay; 60-day episodic weight halved."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chloe.state.db import migrate, close, get_connection
from chloe.memory.store import decay, decay_all

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


def _insert_memory(kind: str, weight: float, age_days: int) -> int:
    conn = get_connection()
    created_at = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO memories (kind, text, weight, salience, confidence, tags, artifact_refs,
                               created_at, updated_at)
        VALUES (?, ?, ?, 0.5, 1.0, '[]', '[]', ?, ?)
        """,
        (kind, f"test memory {kind}", weight, created_at, created_at),
    )
    conn.commit()
    return cursor.lastrowid


def test_decay_formula_episodic_half_life_60():
    new_w = decay(1.0, age_days=60.0, kind="episodic")
    assert abs(new_w - 0.5) < 1e-6


def test_decay_formula_semantic_half_life_180():
    new_w = decay(1.0, age_days=180.0, kind="semantic")
    assert abs(new_w - 0.5) < 1e-6


def test_decay_formula_autobiographical_half_life_365():
    new_w = decay(1.0, age_days=365.0, kind="autobiographical")
    assert abs(new_w - 0.5) < 1e-6


def test_decay_formula_procedural_half_life_90():
    new_w = decay(1.0, age_days=90.0, kind="procedural")
    assert abs(new_w - 0.5) < 1e-6


def test_decay_all_halves_60day_episodic():
    mem_id = _insert_memory("episodic", weight=1.0, age_days=60)

    count = decay_all()
    assert count >= 1

    conn = get_connection()
    row = conn.execute("SELECT weight FROM memories WHERE id = ?", (mem_id,)).fetchone()
    assert abs(row["weight"] - 0.5) < 1e-4


def test_decay_all_skips_fresh_memories():
    mem_id = _insert_memory("episodic", weight=1.0, age_days=0)
    decay_all()
    conn = get_connection()
    row = conn.execute("SELECT weight FROM memories WHERE id = ?", (mem_id,)).fetchone()
    assert abs(row["weight"] - 1.0) < 1e-4


def test_decay_all_autobiographical_slower():
    ep_id = _insert_memory("episodic", weight=1.0, age_days=60)
    auto_id = _insert_memory("autobiographical", weight=1.0, age_days=60)

    decay_all()

    conn = get_connection()
    ep_row = conn.execute("SELECT weight FROM memories WHERE id = ?", (ep_id,)).fetchone()
    auto_row = conn.execute("SELECT weight FROM memories WHERE id = ?", (auto_id,)).fetchone()

    # autobiographical decays slower than episodic for same age
    assert auto_row["weight"] > ep_row["weight"]
