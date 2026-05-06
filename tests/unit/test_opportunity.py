import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from chloe.initiative.opportunity import get_opportunity_vector, CACHE_KEY, CACHE_TTL_MINUTES
from chloe.llm.schemas import OpportunityVector


@pytest.fixture(autouse=True)
def clear_kv_cache(monkeypatch):
    monkeypatch.setattr("chloe.initiative.opportunity.kv_get", lambda key, **kw: None)
    monkeypatch.setattr("chloe.initiative.opportunity.kv_set", lambda key, val: None)


@pytest.mark.asyncio
async def test_first_call_makes_flash_call(monkeypatch):
    mock_flash = AsyncMock(return_value={
        "messages": 0.8, "spotify": 0.6, "calendar": 0.4,
        "notes": 0.7, "web_search": 0.8, "gmail": 0.3, "reminders": 0.4,
    })
    monkeypatch.setattr("chloe.initiative.opportunity.get_llm", lambda: MagicMock(flash=mock_flash))
    monkeypatch.setattr("chloe.initiative.opportunity._gather_context", AsyncMock(return_value={}))

    vec = await get_opportunity_vector()
    assert mock_flash.call_count == 1
    assert vec.messages == 0.8


@pytest.mark.asyncio
async def test_cached_call_skips_flash(monkeypatch):
    now = datetime.utcnow()
    cached_vector = {
        "messages": 0.9, "spotify": 0.5, "calendar": 0.3,
        "notes": 0.6, "web_search": 0.7, "gmail": 0.2, "reminders": 0.3,
    }

    def mock_kv_get(key, **kw):
        if key == CACHE_KEY:
            return {
                "vector": cached_vector,
                "cached_at": now.isoformat(),
            }
        return None

    monkeypatch.setattr("chloe.initiative.opportunity.kv_get", mock_kv_get)

    mock_flash = AsyncMock()
    monkeypatch.setattr("chloe.initiative.opportunity.get_llm", lambda: MagicMock(flash=mock_flash))

    vec = await get_opportunity_vector()
    assert mock_flash.call_count == 0
    assert vec.messages == 0.9


@pytest.mark.asyncio
async def test_stale_cache_refreshes(monkeypatch):
    stale_time = (datetime.utcnow() - timedelta(minutes=CACHE_TTL_MINUTES + 1)).isoformat()

    def mock_kv_get(key, **kw):
        if key == CACHE_KEY:
            return {
                "vector": {"messages": 0.1, "spotify": 0.1, "calendar": 0.1,
                           "notes": 0.1, "web_search": 0.1, "gmail": 0.1, "reminders": 0.1},
                "cached_at": stale_time,
            }
        return None

    monkeypatch.setattr("chloe.initiative.opportunity.kv_get", mock_kv_get)
    monkeypatch.setattr("chloe.initiative.opportunity.kv_set", lambda k, v: None)

    mock_flash = AsyncMock(return_value={
        "messages": 0.8, "spotify": 0.6, "calendar": 0.4,
        "notes": 0.7, "web_search": 0.8, "gmail": 0.3, "reminders": 0.4,
    })
    monkeypatch.setattr("chloe.initiative.opportunity.get_llm", lambda: MagicMock(flash=mock_flash))
    monkeypatch.setattr("chloe.initiative.opportunity._gather_context", AsyncMock(return_value={}))

    vec = await get_opportunity_vector()
    assert mock_flash.call_count == 1
    assert vec.messages == 0.8


@pytest.mark.asyncio
async def test_llm_failure_returns_default(monkeypatch):
    monkeypatch.setattr("chloe.initiative.opportunity.kv_get", lambda k, **kw: None)
    monkeypatch.setattr("chloe.initiative.opportunity.kv_set", lambda k, v: None)
    monkeypatch.setattr("chloe.initiative.opportunity.get_llm", lambda: MagicMock(flash=AsyncMock(return_value=None)))
    monkeypatch.setattr("chloe.initiative.opportunity._gather_context", AsyncMock(return_value={}))

    vec = await get_opportunity_vector()
    assert isinstance(vec, OpportunityVector)
    assert 0.0 <= vec.messages <= 1.0
