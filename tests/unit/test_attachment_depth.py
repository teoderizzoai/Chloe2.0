"""E-11: attachment_depth — delta, silence decay, openness bias, relationship label."""
from __future__ import annotations

from pathlib import Path

import pytest

from chloe.state.db import migrate, close, get_connection
from chloe.persons.attachment import (
    apply_delta, apply_silence_decay, openness_bias, relationship_label,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


@pytest.fixture()
def person_id():
    conn = get_connection()
    cursor = conn.execute(
        "INSERT INTO persons (name, aliases) VALUES ('Teo', '[]')"
    )
    conn.commit()
    return cursor.lastrowid


def test_positive_delta_increases_depth(person_id):
    result = apply_delta(person_id, 0.03)
    assert result > 0.0


def test_negative_delta_decreases_depth(person_id):
    apply_delta(person_id, 0.04)
    result = apply_delta(person_id, -0.05)
    assert result < 0.04


def test_delta_clamped_to_range(person_id):
    result = apply_delta(person_id, 1.0)
    assert result <= 0.05


def test_depth_clamped_to_minus_one_to_one(person_id):
    for _ in range(100):
        apply_delta(person_id, 0.05)
    conn = get_connection()
    row = conn.execute("SELECT attachment_depth FROM persons WHERE id = ?", (person_id,)).fetchone()
    assert row["attachment_depth"] <= 1.0

    for _ in range(100):
        apply_delta(person_id, -0.05)
    row = conn.execute("SELECT attachment_depth FROM persons WHERE id = ?", (person_id,)).fetchone()
    assert row["attachment_depth"] >= -1.0


def test_silence_within_threshold_no_decay(person_id):
    apply_delta(person_id, 0.05)
    depth_before = apply_delta(person_id, 0.0)
    depth_after = apply_silence_decay(person_id, days_since_contact=2.0)
    assert depth_after == depth_before


def test_six_day_silence_decreases_depth(person_id):
    apply_delta(person_id, 0.05)
    apply_delta(person_id, 0.05)
    depth_before = apply_delta(person_id, 0.0)

    depth_after = apply_silence_decay(person_id, days_since_contact=6.0)
    assert depth_after < depth_before


def test_openness_bias_positive_for_positive_depth():
    assert openness_bias(0.8) == pytest.approx(0.12)


def test_openness_bias_zero_for_zero_depth():
    assert openness_bias(0.0) == pytest.approx(0.0)


def test_openness_bias_negative_for_estranged():
    assert openness_bias(-0.5) == pytest.approx(-0.075)


def test_openness_at_depth_08_higher_than_at_zero():
    assert openness_bias(0.8) > openness_bias(0.0)


def test_relationship_labels():
    assert relationship_label(0.8) == "deeply close"
    assert relationship_label(0.5) == "warmly connected"
    assert relationship_label(0.2) == "friendly"
    assert relationship_label(0.0) == "neutral"
    assert relationship_label(-0.3) == "distant"
    assert relationship_label(-0.8) == "estranged"


def test_chat_prompt_includes_relationship_label(person_id):
    """E-11 + E-09: chat_api.build_dynamic_suffix includes relationship prose."""
    import asyncio
    apply_delta(person_id, 0.05)
    apply_delta(person_id, 0.05)

    from chloe.channels.chat_api import build_dynamic_suffix
    suffix = asyncio.run(build_dynamic_suffix(str(person_id), message=""))

    assert "Teo" in suffix
