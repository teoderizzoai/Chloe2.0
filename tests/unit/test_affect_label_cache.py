"""E-04: affect label cache — two calls within 30 min = one LLM call."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from chloe.state.db import migrate, close
from chloe.affect.dims import AffectState

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


@pytest.fixture(autouse=True)
def clear_kv_cache():
    from chloe.state import kv
    kv.delete("affect_label_cache")
    yield
    kv.delete("affect_label_cache")


@pytest.mark.asyncio
async def test_two_calls_within_30min_single_llm_call():
    """Two calls within 30 minutes should only call the LLM once."""
    affect = AffectState(valence=0.2, arousal=0.5, social_pull=0.5, openness=0.6)
    call_count = 0

    async def mock_flash(prompt_file, context, schema):
        nonlocal call_count
        call_count += 1
        return {"label": "gently curious"}

    with patch("chloe.affect.label.get_client") as mock_client:
        mock_client.return_value.flash = mock_flash

        from chloe.affect.label import get_label
        label1 = await get_label(affect)
        label2 = await get_label(affect)

    assert call_count == 1
    assert label1 == label2 == "gently curious"


@pytest.mark.asyncio
async def test_expired_cache_triggers_new_call():
    """A call after the 30-min TTL should call the LLM again."""
    from chloe.state import kv
    from chloe.affect.label import _CACHE_KEY

    affect = AffectState(valence=-0.2, arousal=0.3, social_pull=0.4, openness=0.5)

    old_time = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    kv.set(_CACHE_KEY, {"label": "stale-label", "cached_at": old_time})

    call_count = 0

    async def mock_flash(prompt_file, context, schema):
        nonlocal call_count
        call_count += 1
        return {"label": "freshly pensive"}

    with patch("chloe.affect.label.get_client") as mock_client:
        mock_client.return_value.flash = mock_flash

        from chloe.affect.label import get_label
        label = await get_label(affect)

    assert call_count == 1
    assert label == "freshly pensive"


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_fallback():
    affect = AffectState(valence=0.0, arousal=0.4, social_pull=0.5, openness=0.6)

    with patch("chloe.affect.label.get_client") as mock_client:
        mock_client.return_value.flash = AsyncMock(return_value=None)

        from chloe.affect.label import get_label
        label = await get_label(affect)

    assert isinstance(label, str)
    assert len(label) > 0
