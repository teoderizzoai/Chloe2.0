from __future__ import annotations

from datetime import datetime, timedelta

from chloe.llm.gemini import get_client as get_llm
from chloe.llm.schemas import OpportunityVector
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.observability.logging import get_logger

log = get_logger("initiative.opportunity")

CACHE_KEY = "opp_vector_cache"
CACHE_TTL_MINUTES = 10


async def get_opportunity_vector() -> OpportunityVector:
    """
    Return the current world-opportunity vector.
    Result is cached for 10 minutes in kv to avoid repeated Flash calls.
    """
    cached = kv_get(CACHE_KEY)
    if cached:
        cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
        if datetime.utcnow() - cached_at < timedelta(minutes=CACHE_TTL_MINUTES):
            log.debug("opp_vector_cache_hit")
            return OpportunityVector(**cached["vector"])

    vector = await _compute_vector()
    kv_set(CACHE_KEY, {
        "vector": vector.model_dump(),
        "cached_at": datetime.utcnow().isoformat(),
    })
    return vector


async def _compute_vector() -> OpportunityVector:
    """Build the opportunity vector via a Flash call."""
    now = datetime.now()
    context = await _gather_context(now)

    llm = get_llm()
    result = await llm.flash("opportunity_vector.md", context, schema=OpportunityVector)

    if result is None:
        log.warning("opp_vector_llm_failed_using_defaults")
        return _default_vector(now)

    try:
        return OpportunityVector(**result) if isinstance(result, dict) else result
    except Exception as exc:
        log.warning("opp_vector_parse_error", error=str(exc))
        return _default_vector(now)


async def _gather_context(now: datetime) -> dict:
    """Gather world context for the opportunity vector prompt."""
    calendar_events = []
    try:
        from chloe.tools.calendar import CalendarTool
        cal = CalendarTool()
        result = await cal.execute("read_today", {})
        if result.success:
            calendar_events = result.data.get("events", [])
    except Exception:
        pass

    return {
        "time_of_day": now.strftime("%H:%M"),
        "day_of_week": now.strftime("%A"),
        "hour": now.hour,
        "is_weekend": now.weekday() >= 5,
        "calendar_events_today": calendar_events,
        "last_chat_seen": kv_get("last_chat_seen", default="unknown"),
        "spotify_playing": kv_get("spotify_is_playing", default=False),
    }


def _default_vector(now: datetime) -> OpportunityVector:
    """Return a sensible default when LLM is unavailable."""
    hour = now.hour
    msg_opp = 0.8 if 9 <= hour <= 22 else 0.2
    return OpportunityVector(
        messages=msg_opp,
        spotify=0.5,
        calendar=0.4,
        notes=0.6,
        web_search=0.7,
        gmail=0.3,
        reminders=0.4,
    )
