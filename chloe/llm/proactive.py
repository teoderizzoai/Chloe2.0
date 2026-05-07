from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from chloe.observability.logging import get_logger
from chloe.state.kv import get as kv_get, set as kv_set

log = get_logger("proactive")

PROACTIVE_COOLDOWN_KEY = "proactive_surfaced"
PROACTIVE_COOLDOWN_MIN = 120


@dataclass
class ProactiveOffer:
    topic: str
    suggestion: str
    confidence: float
    evidence: str


def generate_proactive_offer(
    last_message: str,
    recent_topics: list[str],
    now: datetime | None = None,
) -> ProactiveOffer | None:
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
    (time(7, 30),  time(9, 0),   None,         "morning",   "Good morning — want me to check your calendar or the weather?", 0.65),
    (time(17, 30), time(19, 30), [0, 1, 2, 3, 4], "commute", "If you're heading home, I can check traffic.",                 0.60),
    (time(21, 0),  time(23, 0),  None,         "wind_down", "Winding down — anything you want to note or remember from today?", 0.55),
    (time(12, 0),  time(13, 30), [0, 1, 2, 3, 4], "lunch",  "Lunchtime — any plans?",                                        0.50),
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
# Recency signals
# ---------------------------------------------------------------------------

ECHO_TOPICS = {
    "meeting":   ("meeting follow-up",   "Did the meeting go as expected?",                   0.60),
    "doctor":    ("health follow-up",    "How did the appointment go?",                       0.72),
    "interview": ("interview follow-up", "How did the interview go? Thinking of you.",        0.75),
    "flight":    ("travel follow-up",    "Did you land safely?",                              0.80),
    "date":      ("social follow-up",    "How did it go?",                                    0.65),
    "exam":      ("exam follow-up",      "How did the exam go?",                              0.70),
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
    cutoff = (now - timedelta(hours=24)).isoformat()
    data = {k: v for k, v in data.items() if v > cutoff}
    kv_set(PROACTIVE_COOLDOWN_KEY, json.dumps(data))


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None
