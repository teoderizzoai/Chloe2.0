import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.state.db import migrate, close, get_connection

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_humor_detection_under_threshold_no_trait(db):
    from chloe.identity.traits import record_humor_detection
    for _ in range(3):
        record_humor_detection("dry", "mutual")
    trait = db.execute(
        "SELECT id FROM identity_traits WHERE name='finds dry wit charming'"
    ).fetchone()
    assert trait is None


def test_humor_detection_at_threshold_seeds_trait(db):
    from chloe.identity.traits import record_humor_detection
    for _ in range(4):
        record_humor_detection("dry", "mutual")
    trait = db.execute(
        "SELECT weight FROM identity_traits WHERE name='finds dry wit charming'"
    ).fetchone()
    assert trait is not None
    assert trait["weight"] == pytest.approx(0.3)


def test_humor_seed_does_not_duplicate(db):
    from chloe.identity.traits import record_humor_detection
    for _ in range(8):
        record_humor_detection("dry", "mutual")
    count = db.execute(
        "SELECT COUNT(*) as cnt FROM identity_traits WHERE name='finds dry wit charming'"
    ).fetchone()["cnt"]
    assert count == 1


def test_humor_none_kind_is_noop(db):
    from chloe.identity.traits import record_humor_detection
    record_humor_detection(None, None)


@pytest.mark.asyncio
async def test_inside_joke_created_on_first_candidate(db):
    from chloe.memory.inside_jokes import record_inside_joke
    with patch("chloe.memory.inside_jokes._find_existing", return_value=None):
        with patch("chloe.memory.inside_jokes._set_weight") as mock_sw:
            with patch("chloe.memory.store.add", return_value=42):
                result = await record_inside_joke("the whale thing", "Teo said 'kitchen whales again'")
    assert result is not None


@pytest.mark.asyncio
async def test_inside_joke_reinforced_on_repeat(db):
    from chloe.memory.inside_jokes import record_inside_joke
    with patch("chloe.memory.inside_jokes._find_existing", return_value={"id": 1, "weight": 0.85}):
        with patch("chloe.memory.inside_jokes._set_weight") as mock_sw:
            result = await record_inside_joke("the whale thing", "again")
    assert result is None
    mock_sw.assert_called_once()


def test_inside_joke_bonus_applied_when_topic_matches():
    from chloe.memory.retrieval import _apply_inside_joke_bonus, Memory
    from dataclasses import field

    m = Memory(
        id=1, kind="semantic", text="inside joke about whales",
        tags=["inside_joke", "joke_topic:whales"],
        score=0.5,
    )
    _apply_inside_joke_bonus([m], "remember the whales thing?")
    assert m.score > 0.5


def test_inside_joke_bonus_not_applied_when_topic_absent():
    from chloe.memory.retrieval import _apply_inside_joke_bonus, Memory

    m = Memory(
        id=2, kind="semantic", text="inside joke about coffee",
        tags=["inside_joke", "joke_topic:coffee"],
        score=0.5,
    )
    _apply_inside_joke_bonus([m], "talk about something else entirely")
    assert m.score == pytest.approx(0.5)
