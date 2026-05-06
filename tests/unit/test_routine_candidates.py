import pytest
from datetime import datetime, date, timedelta
from unittest.mock import patch
from chloe.initiative.candidates import routine_candidates, mark_routine_done


def _at(hour: int, minute: int, weekday: int = 0) -> datetime:
    """Create a datetime at a given hour:minute on a specific weekday (0=Monday)."""
    base = date(2026, 5, 4)  # A Monday
    delta = (weekday - base.weekday()) % 7
    d = base + timedelta(days=delta)
    return datetime(d.year, d.month, d.day, hour, minute)


def test_morning_checkin_in_window():
    now = _at(8, 30)
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "morning_checkin" in sources


def test_morning_checkin_outside_window():
    now = _at(9, 0)
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "morning_checkin" not in sources


def test_morning_checkin_already_sent():
    now = _at(8, 30)
    def mock_kv_get(key, default=None):
        if "morning_sent" in key:
            return True
        return None
    with patch("chloe.initiative.candidates.kv_get", side_effect=mock_kv_get):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "morning_checkin" not in sources


def test_evening_checkin_in_window():
    now = _at(21, 0)
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "evening_checkin" in sources


def test_sunday_weekly_model_in_window():
    now = _at(3, 30, weekday=6)  # Sunday
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "weekly_self_model" in sources
    assert "consolidation" in sources


def test_weekly_model_not_on_monday():
    now = _at(3, 30, weekday=0)  # Monday
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    sources = [c.source_id for c in candidates]
    assert "weekly_self_model" not in sources


def test_sunday_03_05_returns_2_candidates():
    """03:05 Sunday — in both consolidation and weekly windows → 2 candidates."""
    now = _at(3, 5, weekday=6)
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    assert len(candidates) == 2
    sources = {c.source_id for c in candidates}
    assert "consolidation" in sources
    assert "weekly_self_model" in sources


def test_all_routines_high_pressure():
    now = _at(8, 30)
    with patch("chloe.initiative.candidates.kv_get", return_value=None):
        candidates = routine_candidates(now)
    for c in candidates:
        assert c.pressure == 0.8
