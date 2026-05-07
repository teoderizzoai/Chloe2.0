from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection
from chloe.state.kv import get as kv_get, set as kv_set

log = get_logger("initiative.curiosity")

CURIOSITY_COOLDOWN_DAYS = 7
CURIOSITY_RECENT_DAYS   = 3
CURIOSITY_STALE_DAYS    = 21
CURIOSITY_MAX_PRESSURE  = 0.45


@dataclass
class CuriosityCandidate:
    topic: str
    person_name: str
    question_framing: str
    pressure: float
    evidence: str


def generate_curiosity_candidates() -> list[CuriosityCandidate]:
    conn = get_connection()
    candidates: list[CuriosityCandidate] = []

    persons = conn.execute(
        "SELECT id, name, attachment_depth FROM persons WHERE is_active=1 ORDER BY attachment_depth DESC"
    ).fetchall()

    cooled = _load_cooled_topics()

    for person in persons:
        pid = person["id"]
        pname = person["name"]
        depth = person["attachment_depth"]

        for topic, last_mentioned_at, mention_count in _extract_person_topics(pid, conn):
            if topic in cooled:
                continue

            age_days = _days_since(last_mentioned_at, datetime.utcnow())
            if age_days is None or age_days < CURIOSITY_RECENT_DAYS:
                continue

            stale_bonus = min(0.3, (age_days - CURIOSITY_STALE_DAYS) / 30.0) if age_days >= CURIOSITY_STALE_DAYS else 0.0
            depth_factor = max(0.1, depth)
            freq_factor = min(0.3, mention_count / 10.0)
            pressure = min(CURIOSITY_MAX_PRESSURE, 0.2 * depth_factor + 0.1 * freq_factor + stale_bonus)

            candidates.append(CuriosityCandidate(
                topic=topic,
                person_name=pname,
                question_framing=_generate_framing(topic, pname, age_days),
                pressure=pressure,
                evidence=f"Last mentioned ~{int(age_days)}d ago, {mention_count} time(s) total.",
            ))

    candidates.sort(key=lambda c: c.pressure, reverse=True)
    top = candidates[:2]
    log.debug("curiosity_candidates", count=len(top), topics=[c.topic for c in top])
    return top


def _extract_person_topics(person_id: int, conn) -> list[tuple[str, str, int]]:
    # Query all episodic/chat memories for topic tags
    # (actions table has no person_id column; topic tags are written on chat memories)
    rows = conn.execute(
        """
        SELECT tags, created_at
        FROM memories
        WHERE kind IN ('episodic', 'semantic')
          AND source IN ('chat', 'chat_inbound', 'chat_outbound', 'action')
        ORDER BY created_at DESC
        LIMIT 100
        """,
    ).fetchall()

    topic_data: dict[str, list[str]] = {}
    for row in rows:
        try:
            tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else row["tags"] or []
        except Exception:
            continue
        for tag in tags:
            if tag.startswith("topic:"):
                label = tag[len("topic:"):]
                topic_data.setdefault(label, []).append(row["created_at"])

    result = []
    for label, timestamps in topic_data.items():
        timestamps.sort(reverse=True)
        result.append((label, timestamps[0], len(timestamps)))

    return result


def _days_since(iso_date: str, now: datetime) -> float | None:
    try:
        dt = datetime.fromisoformat(iso_date)
        return (now - dt).total_seconds() / 86400.0
    except Exception:
        return None


FRAMING_TEMPLATES = {
    "sister":   "I've been wondering how your sister is doing lately.",
    "work":     "How has work been lately? Haven't heard much about it recently.",
    "project":  "Whatever happened with that project you mentioned?",
    "health":   "How have you been feeling lately, physically?",
    "friend":   "I was thinking about your friend the other day — how are they?",
    "travel":   "Have you been anywhere interesting recently?",
    "book":     "Did you ever finish that book you were reading?",
    "music":    "Found any new music lately?",
    "food":     "Have you tried anything good to eat recently?",
}

FALLBACK_FRAMING = "I've been curious about {topic} lately — what's going on with that?"


def _generate_framing(topic: str, person_name: str, age_days: float) -> str:
    for key, template in FRAMING_TEMPLATES.items():
        if key in topic.lower():
            return template
    return FALLBACK_FRAMING.format(topic=topic)


def _load_cooled_topics() -> set[str]:
    raw = kv_get("curiosity_cooled_topics")
    if not raw:
        return set()
    try:
        data = json.loads(raw)
        cutoff = (datetime.utcnow() - timedelta(days=CURIOSITY_COOLDOWN_DAYS)).isoformat()
        return {e["topic"] for e in data if e.get("surfaced_at", "") > cutoff}
    except Exception:
        return set()


def mark_curiosity_surfaced(topic: str) -> None:
    raw = kv_get("curiosity_cooled_topics")
    try:
        data = json.loads(raw) if raw else []
    except Exception:
        data = []
    data.append({"topic": topic, "surfaced_at": datetime.utcnow().isoformat()})
    data = data[-30:]
    kv_set("curiosity_cooled_topics", json.dumps(data))


def curiosity_driven_candidates(is_idle: bool = False):
    """Only generate curiosity candidates during idle ticks."""
    if not is_idle:
        return []

    from chloe.initiative.candidates import CandidateAction
    raw = generate_curiosity_candidates()
    candidates = []
    for c in raw:
        candidates.append(CandidateAction(
            tool="gap_flag",
            verb="surface",
            args={
                "subject": f"curiosity: {c.topic}",
                "description": c.evidence,
                "suggested_framing": c.question_framing,
                "kind": "curiosity",
                "reference_id": c.topic,
            },
            intent=f"I'm curious about {c.topic} — {c.question_framing}",
            pressure=c.pressure,
            source="curiosity",
            source_id=f"curiosity:{c.topic}",
        ))
    return candidates
