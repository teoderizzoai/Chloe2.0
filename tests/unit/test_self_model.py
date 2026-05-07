import pytest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch
from chloe.state.db import migrate, close, get_connection
from chloe.identity.self_model import run_weekly_self_model, _assemble_input_pack

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO affect_state (id, valence, arousal, social_pull, openness)"
        " VALUES (1, 0.1, 0.4, 0.5, 0.6)"
    )
    conn.commit()
    yield conn
    close()


def test_assemble_input_pack_has_required_keys(db):
    with patch("chloe.actions.audit.feed_text", return_value=""):
        pack = _assemble_input_pack()
    assert "traits" in pack
    assert "goals" in pack
    assert "wants" in pack
    assert "fears" in pack
    assert "affect" in pack
    assert "as_of" in pack
    assert "held_back_summary" in pack
    assert "voice_drift_context" in pack


@pytest.mark.asyncio
async def test_run_weekly_self_model_writes_belief_and_goal(db):
    mock_output = {
        "self_narrative_belief": "I have been attentive and curious this week.",
        "next_week_intention": "Focus on helping Teo with his project deadlines.",
        "noted_contradictions": [],
        "affect_drift_note": None,
        "restraint_reflection": None,
        "voice_drift_note": None,
    }

    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(return_value=mock_output)
        with patch("chloe.actions.audit.feed_text", return_value=""):
            result = await run_weekly_self_model()

    assert result is not None
    assert "belief_id" in result
    assert "goal_id" in result

    conn = get_connection()
    belief = conn.execute(
        "SELECT text, confidence FROM inner_beliefs WHERE id=?",
        (result["belief_id"],)
    ).fetchone()
    goal = conn.execute(
        "SELECT name FROM inner_goals WHERE id=?",
        (result["goal_id"],)
    ).fetchone()

    assert belief["text"] == "I have been attentive and curious this week."
    assert abs(belief["confidence"] - 0.5) < 0.001
    assert "project deadlines" in goal["name"]


@pytest.mark.asyncio
async def test_run_weekly_self_model_inner_beliefs_grows_by_one(db):
    conn = get_connection()
    count_before = conn.execute("SELECT COUNT(*) as n FROM inner_beliefs").fetchone()["n"]

    mock_output = {
        "self_narrative_belief": "A new belief from this week.",
        "next_week_intention": "New intention.",
        "noted_contradictions": [],
        "affect_drift_note": None,
        "restraint_reflection": None,
        "voice_drift_note": None,
    }

    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(return_value=mock_output)
        with patch("chloe.actions.audit.feed_text", return_value=""):
            await run_weekly_self_model()

    count_after = conn.execute("SELECT COUNT(*) as n FROM inner_beliefs").fetchone()["n"]
    assert count_after == count_before + 1


@pytest.mark.asyncio
async def test_run_weekly_self_model_inner_goals_grows_by_one(db):
    conn = get_connection()
    count_before = conn.execute("SELECT COUNT(*) as n FROM inner_goals").fetchone()["n"]

    mock_output = {
        "self_narrative_belief": "Belief.",
        "next_week_intention": "New goal intention.",
        "noted_contradictions": [],
        "affect_drift_note": None,
        "restraint_reflection": None,
        "voice_drift_note": None,
    }

    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(return_value=mock_output)
        with patch("chloe.actions.audit.feed_text", return_value=""):
            await run_weekly_self_model()

    count_after = conn.execute("SELECT COUNT(*) as n FROM inner_goals").fetchone()["n"]
    assert count_after == count_before + 1


@pytest.mark.asyncio
async def test_llm_none_returns_none(db):
    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(return_value=None)
        with patch("chloe.actions.audit.feed_text", return_value=""):
            result = await run_weekly_self_model()
    assert result is None


@pytest.mark.asyncio
async def test_llm_exception_returns_none(db):
    with patch("chloe.identity.self_model._gemini") as mock_gemini:
        mock_gemini.pro_thinking = AsyncMock(side_effect=Exception("API timeout"))
        with patch("chloe.actions.audit.feed_text", return_value=""):
            result = await run_weekly_self_model()
    assert result is None
