import pytest
import json
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.inner.belief_revision import (
    upsert_belief_with_revision,
    _find_overlapping_belief,
    _is_contradiction,
    get_belief_confidence_summary,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_first_belief_inserted_without_revision(db):
    new_id = upsert_belief_with_revision(
        content="Teo prefers espresso",
        confidence=0.8,
        source="chat",
        tags=["teo", "coffee", "preference"],
    )
    row = db.execute("SELECT * FROM inner_beliefs WHERE id=?", (new_id,)).fetchone()
    assert row is not None
    assert row["archived"] == 0
    assert row["supersedes"] is None


def test_contradicting_belief_archives_old(db):
    old_id = upsert_belief_with_revision(
        content="Teo prefers espresso",
        confidence=0.5,   # old, lower confidence
        source="chat",
        tags=["teo", "coffee", "preference"],
    )
    new_id = upsert_belief_with_revision(
        content="Teo prefers americano",
        confidence=0.85,  # delta=0.35 >= REVISION_CONFIDENCE_DELTA(0.25)
        source="chat",
        tags=["teo", "coffee", "preference"],
    )
    old_row = db.execute("SELECT * FROM inner_beliefs WHERE id=?", (old_id,)).fetchone()
    new_row = db.execute("SELECT * FROM inner_beliefs WHERE id=?", (new_id,)).fetchone()

    assert old_row["archived"] == 1
    assert old_row["superseded_by"] == str(new_id)
    assert new_row["supersedes"] == str(old_id)


def test_revision_creates_autobiographical_memory(db):
    upsert_belief_with_revision("Teo likes tea", 0.5, "chat", ["teo", "tea", "preference"])
    upsert_belief_with_revision("Teo dislikes tea", 0.85, "chat", ["teo", "tea", "preference"])

    mem = db.execute(
        "SELECT * FROM memories WHERE source='belief_revision'"
    ).fetchone()
    assert mem is not None
    assert "updated my understanding" in mem["text"]


def test_similar_confidence_no_revision(db):
    upsert_belief_with_revision("Teo likes espresso", 0.7, "chat", ["teo", "espresso", "preference"])
    upsert_belief_with_revision("Teo likes espresso in the morning", 0.72, "chat", ["teo", "espresso", "preference"])

    archived = db.execute(
        "SELECT COUNT(*) as cnt FROM inner_beliefs WHERE archived=1"
    ).fetchone()["cnt"]
    assert archived == 0


def test_is_contradiction_threshold():
    assert _is_contradiction(0.8, 0.3) is True
    assert _is_contradiction(0.7, 0.72) is False


def test_epistemic_summary_uncertain_flag(db):
    upsert_belief_with_revision("maybe Teo likes jazz", 0.3, "inference", ["teo", "music", "jazz"])
    summary = get_belief_confidence_summary(["teo", "music"])
    assert summary is not None
    assert summary["uncertain"] is True


def test_epistemic_summary_confident_flag(db):
    upsert_belief_with_revision("Teo loves Italian food", 0.9, "direct", ["teo", "food", "italian"])
    summary = get_belief_confidence_summary(["teo", "food"])
    assert summary is not None
    assert summary["uncertain"] is False
