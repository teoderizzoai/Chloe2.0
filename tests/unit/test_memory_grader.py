"""E-07: grade() selects top-K from candidates via mocked Flash."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chloe.state.db import migrate, close
from chloe.affect.dims import AffectState
from chloe.memory.retrieval import Memory
from chloe.memory.store import grade

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


def _make_candidates(n: int) -> list[Memory]:
    return [
        Memory(id=i + 1, kind="episodic", text=f"Memory text {i + 1}", score=1.0 / (i + 1))
        for i in range(n)
    ]


@pytest.mark.asyncio
async def test_grade_returns_exactly_k():
    candidates = _make_candidates(20)
    affect = AffectState()

    mock_resp = {
        "selected": [{"id": i + 1, "relevance_note": f"relevant {i}"} for i in range(5)]
    }

    with patch("chloe.llm.gemini.get_client") as mock_llm:
        mock_llm.return_value.flash = AsyncMock(return_value=mock_resp)
        result = await grade(candidates, "test message", [], affect, keep=5)

    assert len(result) == 5


@pytest.mark.asyncio
async def test_grade_fewer_candidates_than_k_returns_all():
    candidates = _make_candidates(3)
    affect = AffectState()

    result = await grade(candidates, "test message", [], affect, keep=5)
    assert len(result) == 3


@pytest.mark.asyncio
async def test_grade_llm_failure_falls_back_to_first_k():
    candidates = _make_candidates(20)
    affect = AffectState()

    with patch("chloe.llm.gemini.get_client") as mock_llm:
        mock_llm.return_value.flash = AsyncMock(return_value=None)
        result = await grade(candidates, "test message", [], affect, keep=5)

    assert len(result) == 5


@pytest.mark.asyncio
async def test_grade_empty_candidates_returns_empty():
    affect = AffectState()
    result = await grade([], "test message", [], affect, keep=5)
    assert result == []


@pytest.mark.asyncio
async def test_grade_attaches_relevance_note():
    candidates = _make_candidates(10)
    affect = AffectState()

    mock_resp = {
        "selected": [
            {"id": 1, "relevance_note": "highly relevant to the topic"},
            {"id": 2, "relevance_note": "tangentially related"},
        ]
    }

    with patch("chloe.llm.gemini.get_client") as mock_llm:
        mock_llm.return_value.flash = AsyncMock(return_value=mock_resp)
        result = await grade(candidates, "test", [], affect, keep=2)

    assert result[0].relevance_note == "highly relevant to the topic"
    assert result[1].relevance_note == "tangentially related"
