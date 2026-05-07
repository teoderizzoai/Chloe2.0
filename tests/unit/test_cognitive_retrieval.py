import pytest
import json
from pathlib import Path
from unittest.mock import patch
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set
from chloe.memory.cognitive_retrieval import (
    retrieve,
    _fetch_active_beliefs,
    _fetch_affect_summary,
    _fetch_person_context,
    _detect_tensions,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_retrieve_returns_cognitive_result(db):
    with patch("chloe.memory.cognitive_retrieval._fetch_memories", return_value=[]):
        result = retrieve("I want to check in with Teo")
    assert result.intent == "I want to check in with Teo"
    assert isinstance(result.memories, list)
    assert isinstance(result.tensions, list)
    assert isinstance(result.gaps, list)


def test_fetch_affect_summary_defaults_neutral(db):
    summary = _fetch_affect_summary()
    assert summary["label"] == "neutral"


def test_fetch_affect_summary_reads_checkpoint(db):
    kv_set("affect_checkpoint", json.dumps({"valence": 0.7, "arousal": 0.3, "label": "content"}))
    summary = _fetch_affect_summary()
    assert summary["valence"] == pytest.approx(0.7)
    assert summary["label"] == "content"


def test_fetch_active_beliefs_excludes_archived(db):
    conn = get_connection()
    conn.execute(
        "INSERT INTO inner_beliefs (text, confidence, archived, updated_at)"
        " VALUES ('archived belief', 0.9, 1, datetime('now'))"
    )
    conn.execute(
        "INSERT INTO inner_beliefs (text, confidence, archived, updated_at)"
        " VALUES ('active belief', 0.8, 0, datetime('now'))"
    )
    conn.commit()
    beliefs = _fetch_active_beliefs()
    assert all(not b.get("archived") for b in beliefs)
    assert any("active" in b["text"] for b in beliefs)


def test_tension_detection_fires_on_contradiction():
    from dataclasses import dataclass

    @dataclass
    class FakeMem:
        text: str = "I was really energetic today"

    tensions = _detect_tensions(
        intent="I'm tired and exhausted",
        memories=[FakeMem()],
        beliefs=[{"text": "motivated and excited about the project", "confidence": 0.8}],
    )
    assert len(tensions) > 0


def test_tension_detection_no_false_positives():
    tensions = _detect_tensions(
        intent="I want to go for a run",
        memories=[],
        beliefs=[{"text": "enjoys running in the morning", "confidence": 0.7}],
    )
    assert len(tensions) == 0


def test_retrieve_ms_is_populated(db):
    with patch("chloe.memory.cognitive_retrieval._fetch_memories", return_value=[]):
        result = retrieve("test intent")
    assert result.retrieval_ms >= 0
