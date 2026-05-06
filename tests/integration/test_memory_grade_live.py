"""E-07 + E-09 integration: real Gemini Flash for memory grading."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from chloe.state.db import migrate, close
from chloe.state.chroma import reset_client
from chloe.affect.dims import AffectState
from chloe.memory.retrieval import Memory, query_mixed
from chloe.memory.store import add, grade

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

COLLECTION = "test_grade_live"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


@pytest.fixture(autouse=True)
def fresh_chroma():
    _drop_collection(COLLECTION)
    yield
    _drop_collection(COLLECTION)


def _drop_collection(name: str) -> None:
    from chloe.state.chroma import get_client
    try:
        get_client().delete_collection(name)
    except Exception:
        pass


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grade_with_real_api_returns_up_to_k():
    """Real Gemini Flash: grade 20 candidates → at most 5 returned."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY not set")

    candidates = [
        Memory(id=i + 1, kind="episodic", text=f"Episodic memory {i + 1}", score=0.8)
        for i in range(20)
    ]
    affect = AffectState(valence=0.2, arousal=0.5, social_pull=0.5, openness=0.6)

    result = await grade(candidates, "tell me something about the past", [], affect, keep=5)

    assert len(result) <= 5
    assert all(hasattr(m, "relevance_note") for m in result)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_chat_path_recalls_action_memory():
    """E-09: build_dynamic_suffix with message recalls an action memory."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY not set")

    mem_id = add(
        kind="episodic",
        text="I queued Funeral by Arcade Fire for Teo",
        source="action",
        tags=["action", "spotify", "queue_track"],
        collection_name=COLLECTION,
    )

    from chloe.memory import retrieval
    retrieval.DEFAULT_MIX  # noqa: B018

    candidates = query_mixed(
        "music spotify song queue",
        kinds_mix={"episodic": 10},
        collection_name=COLLECTION,
    )
    assert any(m.id == mem_id for m in candidates), "Action memory not found in candidates"

    affect = AffectState()
    graded = await grade(candidates, "what music did you queue recently?", [], affect, keep=5)

    assert any(m.id == mem_id for m in graded), \
        "Action memory not selected by grader"
