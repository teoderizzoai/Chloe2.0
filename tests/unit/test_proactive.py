import pytest
import json
from datetime import datetime, timedelta
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
    now = datetime(2026, 5, 6, 8, 15)
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
    assert any("go" in o.suggestion or "ended" in o.suggestion for o in offers)


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
    now = datetime(2026, 5, 6, 11, 0)
    result = generate_proactive_offer("hello", [], now=now)
    assert result is None


def test_generate_returns_offer_morning(db):
    now = datetime(2026, 5, 6, 8, 15)
    result = generate_proactive_offer("hi", [], now=now)
    assert result is not None
    assert result.confidence >= 0.55
