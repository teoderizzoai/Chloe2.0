"""E-04 integration: real Gemini Flash call for affect label."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_label_real_api_returns_string():
    """Real Gemini API call: get_label returns a non-empty string."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY not set")

    from chloe.affect.label import get_label
    affect = AffectState(valence=0.4, arousal=0.6, social_pull=0.5, openness=0.7)
    label = await get_label(affect)

    assert isinstance(label, str)
    assert len(label.strip()) > 0
    assert len(label) < 60  # should be a short label


@pytest.mark.integration
@pytest.mark.asyncio
async def test_second_call_uses_cache():
    """Two consecutive calls → second returns cached value."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY not set")

    from chloe.affect.label import get_label
    from chloe.state import kv

    affect = AffectState(valence=0.2, arousal=0.5, social_pull=0.5, openness=0.6)

    label1 = await get_label(affect)
    cached = kv.get("affect_label_cache")
    assert cached is not None

    label2 = await get_label(affect)
    assert label1 == label2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_cache_triggers_fresh_call():
    """Setting cache 31 min old → fresh API call on next get_label."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GEMINI_API_KEY not set")

    from chloe.affect.label import get_label, _CACHE_KEY
    from chloe.state import kv

    old_time = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    kv.set(_CACHE_KEY, {"label": "stale-value", "cached_at": old_time})

    affect = AffectState(valence=-0.3, arousal=0.2, social_pull=0.3, openness=0.4)
    label = await get_label(affect)

    assert label != "stale-value"
    assert isinstance(label, str)
    assert len(label.strip()) > 0
