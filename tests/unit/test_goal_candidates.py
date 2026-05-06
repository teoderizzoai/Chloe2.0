import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from chloe.initiative.candidates import goal_driven_candidates, CandidateAction


def _make_goal(tags=None, days_old=1, **kwargs):
    now = datetime.utcnow()
    return {
        "id": "g1",
        "title": "Build Teo a workout playlist",
        "tags": tags or ["playlist"],
        "completed": False,
        "last_progress_at": (now - timedelta(days=days_old)).isoformat(),
        "created_at": (now - timedelta(days=days_old + 1)).isoformat(),
        **kwargs,
    }


def test_playlist_goal_produces_spotify_candidate():
    goals = [_make_goal(tags=["playlist"])]
    candidates = goal_driven_candidates(goals)
    assert any(c.tool == "spotify" and c.verb == "build_playlist" for c in candidates)


def test_research_goal_produces_web_search():
    goals = [_make_goal(tags=["research"], title="Marine biology")]
    candidates = goal_driven_candidates(goals)
    assert any(c.tool == "web_search" for c in candidates)


def test_writing_goal_produces_notes_candidate():
    goals = [_make_goal(tags=["writing"], title="Start essay")]
    candidates = goal_driven_candidates(goals)
    assert any(c.tool == "notes" for c in candidates)


def test_completed_goal_excluded():
    goals = [_make_goal(tags=["playlist"], completed=True)]
    candidates = goal_driven_candidates(goals)
    assert len(candidates) == 0


def test_stale_goal_triggers_fail(monkeypatch):
    stale_goals = [_make_goal(tags=["playlist"], days_old=20)]

    failed = []
    monkeypatch.setattr("chloe.initiative.candidates.fail_stale_goal", lambda gid: failed.append(gid))

    candidates = goal_driven_candidates(stale_goals)
    assert len(candidates) == 0
    assert "g1" in failed


def test_source_field_is_goal():
    goals = [_make_goal(tags=["playlist"])]
    candidates = goal_driven_candidates(goals)
    for c in candidates:
        assert c.source == "goal"
        assert c.source_id == "g1"
