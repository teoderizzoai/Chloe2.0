# E-04 · `affect/label.py` — lazy Flash labeler

## Overview

Implement `get_label(affect: AffectState) -> str`. Makes a Flash call using `affect_label.md` prompt. Caches result for 30 minutes in `kv["affect_label_cache"]`. Returns cached value within 30 minutes; new Flash call after cache expires.

## Context

The affect label is a human-readable one-liner like "quietly curious and warm" that describes Chloe's current state. It's displayed in the mobile "Now" tab and injected into the chat prompt as part of the tone block. Generating it requires the Flash LLM (it's a creative description, not a pure computation), but doesn't need to happen more than once per 30 minutes — Chloe's affect changes slowly and the label is only for display/hint purposes.

## Implementation

```python
# chloe/affect/label.py

from datetime import datetime, timedelta
from chloe.affect.dims import AffectState
from chloe.llm.gemini import get_client as get_llm
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.observability.logging import get_logger

log = get_logger("affect.label")

CACHE_KEY = "affect_label_cache"
CACHE_TTL_MINUTES = 30
FALLBACK_LABEL = "present and attentive"


async def get_label(affect: AffectState) -> str:
    """
    Return a human-readable label for the current affect state.
    Cached for 30 minutes.
    """
    cached = kv_get(CACHE_KEY)
    if cached:
        cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01T00:00:00"))
        if datetime.utcnow() - cached_at < timedelta(minutes=CACHE_TTL_MINUTES):
            log.debug("affect_label_cache_hit", label=cached["label"])
            return cached["label"]

    label = await _generate_label(affect)
    kv_set(CACHE_KEY, {
        "label": label,
        "cached_at": datetime.utcnow().isoformat(),
    })
    return label


async def _generate_label(affect: AffectState) -> str:
    """Call Flash to generate an affect label."""
    payload = {
        "valence": round(affect.valence, 2),
        "arousal": round(affect.arousal, 2),
        "social_pull": round(affect.social_pull, 2),
        "openness": round(affect.openness, 2),
    }

    from chloe.llm.schemas import AffectLabel
    llm = get_llm()
    result = await llm.flash("affect_label.md", payload, schema=AffectLabel)

    if result is None:
        log.warning("affect_label_llm_failed", using_fallback=FALLBACK_LABEL)
        return FALLBACK_LABEL

    if isinstance(result, dict):
        return result.get("label", FALLBACK_LABEL)
    return getattr(result, "label", FALLBACK_LABEL)
```

## `affect_label.md` prompt template

```markdown
# Affect Label

Translate Chloe's current 4D emotional state into a short, natural-sounding description
(3–8 words) that captures the overall emotional quality.

## Current state
- Valence: {{valence}} (range -1.0=very negative to 1.0=very positive)
- Arousal: {{arousal}} (range 0.0=calm to 1.0=energetic)
- Social pull: {{social_pull}} (range 0.0=withdrawn to 1.0=very sociable)
- Openness: {{openness}} (range 0.0=closed to 1.0=very receptive)

## Examples
- (0.3, 0.5, 0.6, 0.7) → "warm and gently curious"
- (-0.2, 0.3, 0.3, 0.4) → "a little subdued, quietly present"
- (0.6, 0.8, 0.9, 0.8) → "bright and eager to connect"
- (0.0, 0.2, 0.2, 0.5) → "calm and inwardly attentive"

Return JSON: {"label": "your description here"}
```

## `AffectLabel` schema (in `llm/schemas.py`)

```python
class AffectLabel(BaseModel):
    label: str = Field(min_length=3, max_length=100)
```

## Dependencies

- E-03 (`affect/dims.py` — `AffectState` dataclass).
- F-05 (`llm/gemini.py` — Flash call).
- F-06 (`llm/schemas.py` — `AffectLabel` schema).
- F-08 (`state/kv.py` — caching).

## Testing

### Unit tests — `tests/unit/test_affect_label.py`

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from chloe.affect.dims import AffectState
from chloe.affect.label import get_label, CACHE_KEY, CACHE_TTL_MINUTES


@pytest.fixture(autouse=True)
def clear_cache(monkeypatch):
    monkeypatch.setattr("chloe.affect.label.kv_get", lambda k, **kw: None)
    monkeypatch.setattr("chloe.affect.label.kv_set", lambda k, v: None)


@pytest.mark.asyncio
async def test_first_call_makes_flash_call(monkeypatch):
    mock_flash = AsyncMock(return_value={"label": "warm and curious"})
    monkeypatch.setattr("chloe.affect.label.get_llm", lambda: MagicMock(flash=mock_flash))

    result = await get_label(AffectState(0.3, 0.5, 0.6, 0.7))
    assert result == "warm and curious"
    assert mock_flash.call_count == 1


@pytest.mark.asyncio
async def test_cached_call_skips_flash(monkeypatch):
    now = datetime.utcnow()

    def mock_kv_get(key, **kw):
        if key == CACHE_KEY:
            return {"label": "cached label", "cached_at": now.isoformat()}
        return None

    monkeypatch.setattr("chloe.affect.label.kv_get", mock_kv_get)
    mock_flash = AsyncMock()
    monkeypatch.setattr("chloe.affect.label.get_llm", lambda: MagicMock(flash=mock_flash))

    result = await get_label(AffectState())
    assert result == "cached label"
    assert mock_flash.call_count == 0


@pytest.mark.asyncio
async def test_stale_cache_refreshes(monkeypatch):
    stale_time = (datetime.utcnow() - timedelta(minutes=CACHE_TTL_MINUTES + 1)).isoformat()

    def mock_kv_get(key, **kw):
        if key == CACHE_KEY:
            return {"label": "old label", "cached_at": stale_time}
        return None

    monkeypatch.setattr("chloe.affect.label.kv_get", mock_kv_get)
    monkeypatch.setattr("chloe.affect.label.kv_set", lambda k, v: None)

    mock_flash = AsyncMock(return_value={"label": "new label"})
    monkeypatch.setattr("chloe.affect.label.get_llm", lambda: MagicMock(flash=mock_flash))

    result = await get_label(AffectState())
    assert result == "new label"
    assert mock_flash.call_count == 1


@pytest.mark.asyncio
async def test_llm_failure_returns_fallback(monkeypatch):
    monkeypatch.setattr("chloe.affect.label.kv_get", lambda k, **kw: None)
    monkeypatch.setattr("chloe.affect.label.kv_set", lambda k, v: None)
    monkeypatch.setattr("chloe.affect.label.get_llm", lambda: MagicMock(flash=AsyncMock(return_value=None)))

    result = await get_label(AffectState())
    assert isinstance(result, str)
    assert len(result) > 0  # Returns fallback, not empty


@pytest.mark.asyncio
async def test_two_calls_within_30_min_one_flash(monkeypatch):
    """Two calls within 30 minutes should make exactly one Flash call."""
    flash_count = 0
    saved_cache = {}

    async def mock_flash(prompt, payload, schema=None):
        nonlocal flash_count
        flash_count += 1
        return {"label": "calm and open"}

    def mock_kv_get(key, **kw):
        return saved_cache.get(key)

    def mock_kv_set(key, val):
        saved_cache[key] = val

    monkeypatch.setattr("chloe.affect.label.kv_get", mock_kv_get)
    monkeypatch.setattr("chloe.affect.label.kv_set", mock_kv_set)
    monkeypatch.setattr("chloe.affect.label.get_llm", lambda: MagicMock(flash=mock_flash))

    await get_label(AffectState())
    await get_label(AffectState())

    assert flash_count == 1
```

## Acceptance criteria

- Two calls within 30 minutes → exactly one Flash call.
- Call after 31 minutes → second Flash call made.
- LLM failure → returns fallback string `"present and attentive"`, no exception raised.
- Cache key `"affect_label_cache"` written to `kv` with `label` and `cached_at` fields.
