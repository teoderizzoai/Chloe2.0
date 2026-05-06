from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chloe.state import kv
from chloe.llm.gemini import get_client
from chloe.observability.logging import get_logger

log = get_logger("affect.label")

_CACHE_MINUTES = 30
_CACHE_KEY = "affect_label_cache"


async def get_label(affect) -> str:
    """
    Return a short natural-language label for the current affect state.
    Cached in KV for 30 minutes; calls Gemini Flash on cache miss.
    """
    cached = kv.get(_CACHE_KEY)
    if cached:
        try:
            cached_at = datetime.fromisoformat(cached["cached_at"])
            if datetime.now(timezone.utc) - cached_at < timedelta(minutes=_CACHE_MINUTES):
                return cached["label"]
        except Exception:
            pass

    label = await _call_llm(affect)
    kv.set(_CACHE_KEY, {
        "label": label,
        "cached_at": datetime.now(timezone.utc).isoformat(),
    })
    log.info("affect_label_refreshed", label=label)
    return label


async def _call_llm(affect) -> str:
    from chloe.llm.schemas import AffectLabelResult

    llm = get_client()
    result = await llm.flash(
        "affect_label.md",
        {
            "valence": round(affect.valence, 3),
            "arousal": round(affect.arousal, 3),
            "social_pull": round(affect.social_pull, 3),
            "openness": round(affect.openness, 3),
        },
        schema=AffectLabelResult,
    )
    if result and isinstance(result, dict) and result.get("label"):
        return str(result["label"])
    return _fallback(affect)


def _fallback(affect) -> str:
    if affect.valence > 0.3:
        return "content"
    if affect.valence < -0.3:
        return "pensive"
    if affect.arousal > 0.7:
        return "energized"
    if affect.social_pull > 0.7:
        return "reaching out"
    return "steady"
