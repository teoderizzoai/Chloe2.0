# Y-06 · Predictive Intent Surfacing

## Overview

Before generating a chat response, Chloe checks whether there's a related topic she knows about that Teo didn't ask but would likely want. Based on behavioral patterns stored in KV (time-of-day habits, day-of-week patterns, recent calendar context, and conversation history), she generates an optional `ProactiveOffer` that is injected into the system prompt tail as a soft suggestion. The model decides whether to include it naturally.

## Context

A companion that only answers what's asked feels like a search engine. A companion that anticipates — "you usually ask about traffic on Friday evenings, should I check?" or "you mentioned you had a meeting at 2pm, how did it go?" — feels alive.

This is not a separate action or message. It's a **prompt injection**: a soft note to the model saying "you might also want to mention X." The model has full discretion over whether and how to surface it. This avoids the hard problem of deciding programmatically whether to say something — the LLM is better at that than a rule engine.

The prediction engine scores candidate topics by:
1. **Temporal pattern match** — is this a time of day when Chloe usually covers this topic?
2. **Recency signal** — was this topic raised in the last 1-3 turns?
3. **Calendar signal** — is there an upcoming or just-past event that relates?
4. **Explicit habit** — stored behavioral patterns (e.g., "Friday evening → traffic check").

**When:** Phase Y. Depends on `state/kv.py`, calendar tool, memory/retrieval, initiative/opportunity.

---

## New module: `llm/proactive.py`

```python
# chloe/llm/proactive.py
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from chloe.observability.logging import get_logger
from chloe.state.kv import get as kv_get, set as kv_set

log = get_logger("proactive")

PROACTIVE_COOLDOWN_KEY = "proactive_surfaced"
PROACTIVE_COOLDOWN_MIN = 120    # don't re-surface same topic within 2 hours


@dataclass
class ProactiveOffer:
    topic: str
    suggestion: str             # the soft note injected into the system prompt
    confidence: float           # 0.0–1.0, how likely Teo would want this
    evidence: str               # why this was predicted


def generate_proactive_offer(
    last_message: str,
    recent_topics: list[str],
    now: datetime | None = None,
) -> ProactiveOffer | None:
    """
    Returns a ProactiveOffer if there's a high-confidence prediction, else None.
    Confidence threshold: 0.55 (only surface when reasonably sure).
    """
    if now is None:
        now = datetime.now()

    candidates: list[ProactiveOffer] = []

    candidates.extend(_temporal_patterns(now))
    candidates.extend(_calendar_signals(now))
    candidates.extend(_recency_signals(last_message, recent_topics))

    if not candidates:
        return None

    best = max(candidates, key=lambda c: c.confidence)
    if best.confidence < 0.55:
        return None

    if _recently_surfaced(best.topic, now):
        return None

    _mark_surfaced(best.topic, now)
    log.info("proactive_offer_generated", topic=best.topic, confidence=best.confidence)
    return best


# ---------------------------------------------------------------------------
# Temporal patterns
# ---------------------------------------------------------------------------

TEMPORAL_PATTERNS = [
    # (time_start, time_end, weekdays, topic, suggestion, confidence)
    # weekdays: list of int (0=Mon, 6=Sun) or None for every day
    (time(7, 30), time(9, 0),  None,      "morning",   "Good morning — want me to check your calendar or the weather?",       0.65),
    (time(17, 30), time(19, 30), [0,1,2,3,4], "commute", "If you're heading home, I can check traffic.",                      0.60),
    (time(21, 0), time(23, 0),  None,      "wind_down", "Winding down — anything you want to note or remember from today?",   0.55),
    (time(12, 0), time(13, 30), [0,1,2,3,4], "lunch",   "Lunchtime — any plans?",                                            0.50),
]


def _temporal_patterns(now: datetime) -> list[ProactiveOffer]:
    t = now.time()
    wd = now.weekday()
    results = []
    for t_start, t_end, weekdays, topic, suggestion, conf in TEMPORAL_PATTERNS:
        if not (t_start <= t <= t_end):
            continue
        if weekdays is not None and wd not in weekdays:
            continue
        results.append(ProactiveOffer(
            topic=topic,
            suggestion=suggestion,
            confidence=conf,
            evidence=f"Time-of-day pattern: {t_start}–{t_end}",
        ))
    return results


# ---------------------------------------------------------------------------
# Calendar signals
# ---------------------------------------------------------------------------

def _calendar_signals(now: datetime) -> list[ProactiveOffer]:
    """
    Check the KV key `upcoming_events_summary` (written by the calendar tool poller).
    If an event ended in the last 2h or starts in the next 30 min, generate a signal.
    """
    raw = kv_get("upcoming_events_summary")
    if not raw:
        return []

    try:
        events = json.loads(raw)
    except Exception:
        return []

    offers = []
    for ev in events[:5]:
        start = _parse_dt(ev.get("start", ""))
        end = _parse_dt(ev.get("end", ""))
        title = ev.get("title", "your event")[:40]

        if start and 0 <= (start - now).total_seconds() <= 1800:
            offers.append(ProactiveOffer(
                topic=f"upcoming:{title}",
                suggestion=f"You have '{title}' starting soon — anything you need?",
                confidence=0.70,
                evidence=f"Calendar: event starting at {start.strftime('%H:%M')}",
            ))

        if end and 0 <= (now - end).total_seconds() <= 7200:
            offers.append(ProactiveOffer(
                topic=f"past:{title}",
                suggestion=f"Your '{title}' just ended — how did it go?",
                confidence=0.65,
                evidence=f"Calendar: event ended at {end.strftime('%H:%M')}",
            ))

    return offers


# ---------------------------------------------------------------------------
# Recency signals — topic echo
# ---------------------------------------------------------------------------

ECHO_TOPICS = {
    "meeting":  ("meeting follow-up",  "Did the meeting go as expected?",        0.60),
    "doctor":   ("health follow-up",   "How did the appointment go?",            0.72),
    "interview":("interview follow-up","How did the interview go? Thinking of you.", 0.75),
    "flight":   ("travel follow-up",   "Did you land safely?",                   0.80),
    "date":     ("social follow-up",   "How did it go?",                         0.65),
    "exam":     ("exam follow-up",     "How did the exam go?",                   0.70),
}


def _recency_signals(last_message: str, recent_topics: list[str]) -> list[ProactiveOffer]:
    combined = (last_message + " " + " ".join(recent_topics)).lower()
    results = []
    for keyword, (topic, suggestion, conf) in ECHO_TOPICS.items():
        if keyword in combined:
            results.append(ProactiveOffer(
                topic=topic,
                suggestion=suggestion,
                confidence=conf,
                evidence=f"Topic '{keyword}' appeared in recent context.",
            ))
    return results


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------

def _recently_surfaced(topic: str, now: datetime) -> bool:
    raw = kv_get(PROACTIVE_COOLDOWN_KEY)
    if not raw:
        return False
    try:
        data = json.loads(raw)
        entry = data.get(topic)
        if not entry:
            return False
        surfaced_at = datetime.fromisoformat(entry)
        return (now - surfaced_at).total_seconds() < PROACTIVE_COOLDOWN_MIN * 60
    except Exception:
        return False


def _mark_surfaced(topic: str, now: datetime) -> None:
    raw = kv_get(PROACTIVE_COOLDOWN_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {}
    data[topic] = now.isoformat()
    # Prune old entries (older than 24h)
    cutoff = (now - timedelta(hours=24)).isoformat()
    data = {k: v for k, v in data.items() if v > cutoff}
    kv_set(PROACTIVE_COOLDOWN_KEY, json.dumps(data))


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None
```

---

## Injection into system prompt

```python
# In llm/prompts.py:
from chloe.llm.proactive import generate_proactive_offer

async def build_system_prompt(last_message: str, recent_topics: list[str]) -> str:
    # ... existing prompt assembly ...

    offer = generate_proactive_offer(last_message, recent_topics)
    proactive_block = ""
    if offer:
        proactive_block = (
            f"\n\n[Proactive suggestion (use naturally or ignore if it doesn't fit): "
            f"{offer.suggestion}]"
        )

    return base_prompt + proactive_block
```

---

## Behavioral pattern learning

Over time, Chloe should learn which proactive offers Teo engages with vs. ignores. This is tracked simply:

```python
# After each exchange, check if the proactive topic appeared in Teo's reply:
def _score_proactive_outcome(offer_topic: str, teo_reply: str) -> None:
    """Update engagement score for this offer type."""
    raw = kv_get("proactive_engagement_scores") or "{}"
    scores = json.loads(raw)
    engaged = any(word in teo_reply.lower() for word in offer_topic.split(":"))
    current = scores.get(offer_topic, {"hits": 0, "misses": 0})
    if engaged:
        current["hits"] += 1
    else:
        current["misses"] += 1
    scores[offer_topic] = current
    kv_set("proactive_engagement_scores", json.dumps(scores))
```

The engagement score is used to de-weight low-performing offers in a future pass. For now it's write-only — the data accumulates and can be used in a later iteration.

---

## Testing

### `tests/unit/test_proactive.py`

```python
import pytest
import json
from datetime import datetime, time, timedelta
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from chloe.state.kv import set as kv_set, get as kv_get
from chloe.llm.proactive import (
    generate_proactive_offer,
    _temporal_patterns,
    _recency_signals,
    _calendar_signals,
    _recently_surfaced,
    _mark_surfaced,
)

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield get_connection()
    close()


def test_morning_temporal_pattern():
    now = datetime(2026, 5, 6, 8, 15)    # 8:15 AM Tuesday
    offers = _temporal_patterns(now)
    assert any(o.topic == "morning" for o in offers)


def test_evening_commute_only_weekday():
    monday = datetime(2026, 5, 4, 18, 0)
    offers = _temporal_patterns(monday)
    assert any(o.topic == "commute" for o in offers)

    sunday = datetime(2026, 5, 10, 18, 0)
    offers = _temporal_patterns(sunday)
    assert not any(o.topic == "commute" for o in offers)


def test_recency_signal_meeting():
    offers = _recency_signals("just got out of my meeting", [])
    assert len(offers) > 0
    assert any("meeting" in o.topic for o in offers)


def test_recency_signal_interview_high_confidence():
    offers = _recency_signals("nervous about my interview tomorrow", [])
    assert any(o.confidence >= 0.7 for o in offers)


def test_calendar_signal_upcoming_event(db):
    now = datetime(2026, 5, 6, 14, 0)
    start = (now + timedelta(minutes=20)).isoformat()
    end = (now + timedelta(minutes=80)).isoformat()
    kv_set("upcoming_events_summary", json.dumps([
        {"title": "Team sync", "start": start, "end": end}
    ]))
    offers = _calendar_signals(now)
    assert any("Team sync" in o.suggestion for o in offers)


def test_calendar_signal_past_event(db):
    now = datetime(2026, 5, 6, 14, 0)
    start = (now - timedelta(hours=2)).isoformat()
    end = (now - timedelta(hours=1)).isoformat()
    kv_set("upcoming_events_summary", json.dumps([
        {"title": "Doctor visit", "start": start, "end": end}
    ]))
    offers = _calendar_signals(now)
    assert any("ended" in o.suggestion or "go" in o.suggestion for o in offers)


def test_cooldown_prevents_repeat(db):
    now = datetime(2026, 5, 6, 8, 15)
    _mark_surfaced("morning", now)
    assert _recently_surfaced("morning", now + timedelta(minutes=30)) is True


def test_cooldown_expired_allows_resurface(db):
    past = datetime(2026, 5, 6, 6, 0)
    now = datetime(2026, 5, 6, 8, 30)
    _mark_surfaced("morning", past)
    assert _recently_surfaced("morning", now) is False


def test_generate_returns_none_below_threshold(db):
    now = datetime(2026, 5, 6, 11, 0)    # no pattern matches at 11am midweek
    result = generate_proactive_offer("hello", [], now=now)
    assert result is None


def test_generate_returns_offer_morning(db):
    now = datetime(2026, 5, 6, 8, 15)
    result = generate_proactive_offer("hi", [], now=now)
    assert result is not None
    assert result.confidence >= 0.55
```

---

## Dependencies

- `state/kv.py` — cooldown and engagement score storage.
- `upcoming_events_summary` KV key (written by calendar polling in D-07 opportunity vector).
- `llm/prompts.py` — injection point.

## Acceptance criteria

- A message sent at 8:15 AM generates a `morning` proactive offer with confidence ≥ 0.60.
- A message mentioning "interview" generates a follow-up offer with confidence ≥ 0.70.
- The same offer topic is not surfaced twice within 2 hours (cooldown).
- An upcoming calendar event (<30 min away) generates an offer with confidence ≥ 0.70.
- A completed calendar event (<2h ago) generates a follow-up offer.
- No offer generated when all candidates are below confidence 0.55.
- Engagement tracking (hits/misses) written to KV after each exchange with an active offer.
