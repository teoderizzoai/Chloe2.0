# D-04 · `initiative/candidates.py` — goal-driven candidates

## Overview

Implement `goal_driven_candidates(goals) -> list[CandidateAction]`. Each active goal exposes a `next_step()` function (registered at goal creation) that returns the most logical next action. Stale goals (no progress in 14 days) get `fail_stale_goal()` called instead of generating candidates.

## Context

Goals are longer-horizon intentions that Chloe tracks (e.g., "research marine biology", "build a playlist for Teo's workout"). Goal-driven candidates are different from pressure-driven ones: they represent structured progress toward known endpoints rather than relief of unmet needs. The `next_step_hint` field on the `inner_goals` table provides the initiative engine with a free-text hint about what the next concrete action should be — the mapping table resolves this into a `CandidateAction`.

## Next-step registry

```python
# chloe/initiative/goal_steps.py

from chloe.initiative.candidates import CandidateAction

# Maps goal tags/categories to a next-step factory function
# Each factory receives the goal record and returns a CandidateAction or None

def _playlist_next_step(goal: dict) -> CandidateAction | None:
    return CandidateAction(
        tool="spotify", verb="build_playlist",
        args={"name": goal.get("title", "New playlist"), "description": goal.get("description", ""), "track_uris": []},
        intent=f"Make progress on goal: {goal.get('title', 'playlist goal')}",
        pressure=min(goal.get("progress", 0.5) + 0.2, 1.0),  # Pressure grows with investment
        source="goal",
        source_id=goal.get("id", ""),
    )


def _research_next_step(goal: dict) -> CandidateAction | None:
    topic = goal.get("title", "unknown topic")
    return CandidateAction(
        tool="web_search", verb="search",
        args={"query": f"{topic} interesting facts"},
        intent=f"Research for goal: {topic}",
        pressure=0.5,
        source="goal",
        source_id=goal.get("id", ""),
    )


def _writing_next_step(goal: dict) -> CandidateAction | None:
    return CandidateAction(
        tool="notes", verb="append",
        args={"path": f"goals/{goal.get('id', 'note')}.md", "text": ""},
        intent=f"Make progress on writing goal: {goal.get('title', '')}",
        pressure=0.5,
        source="goal",
        source_id=goal.get("id", ""),
    )


GOAL_STEP_REGISTRY = {
    "playlist":  _playlist_next_step,
    "research":  _research_next_step,
    "writing":   _writing_next_step,
    "note":      _writing_next_step,
}
```

## Implementation

```python
# In chloe/initiative/candidates.py — add goal_driven_candidates

from datetime import datetime, timedelta
from chloe.initiative.goal_steps import GOAL_STEP_REGISTRY
from chloe.observability.logging import get_logger

log = get_logger("initiative.goals")
STALE_THRESHOLD_DAYS = 14


def goal_driven_candidates(goals: list | None = None) -> list[CandidateAction]:
    """
    Build candidate actions from active goals.
    goals: optional override list (for testing); if None, reads from DB.
    """
    if goals is None:
        goals = _load_goals()

    candidates = []
    stale_cutoff = (datetime.utcnow() - timedelta(days=STALE_THRESHOLD_DAYS)).isoformat()

    for goal in goals:
        if goal.get("completed"):
            continue

        last_progress = goal.get("last_progress_at") or goal.get("created_at", "")
        if last_progress < stale_cutoff:
            fail_stale_goal(goal["id"])
            continue

        # Find a factory for this goal's category/tags
        factory = None
        for tag in goal.get("tags", []):
            factory = GOAL_STEP_REGISTRY.get(tag)
            if factory:
                break

        if factory is None:
            # Fallback: suggest a web_search
            factory = lambda g: CandidateAction(
                tool="web_search", verb="search",
                args={"query": g.get("title", "interesting topic")},
                intent=f"Explore goal: {g.get('title', '')}",
                pressure=0.4,
                source="goal",
                source_id=g.get("id", ""),
            )

        candidate = factory(goal)
        if candidate:
            candidates.append(candidate)

    log.debug("goal_candidates", count=len(candidates))
    return candidates


def fail_stale_goal(goal_id: str) -> None:
    """Mark a goal as stale/failed and create a memory."""
    from chloe.state.db import get_connection
    from chloe.actions.schema import ulid
    conn = get_connection()
    conn.execute("UPDATE inner_goals SET completed=1, outcome='stale' WHERE id=?", (goal_id,))
    memory_id = ulid()
    conn.execute(
        """INSERT INTO memories (id, kind, text, source, source_ref, artifact_refs, weight, tags, created_at)
           VALUES (?, 'episodic', ?, 'goal_stale', ?, '[]', 0.5, '["goal","stale"]', ?)""",
        (memory_id, f"Goal went stale and was abandoned: {goal_id}", goal_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    log.info("goal_stale", goal_id=goal_id)


def _load_goals() -> list[dict]:
    from chloe.state.db import get_connection
    import json
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM inner_goals WHERE completed IS NULL OR completed = 0"
    ).fetchall()
    goals = []
    for row in rows:
        g = dict(row)
        if isinstance(g.get("tags"), str):
            g["tags"] = json.loads(g["tags"])
        goals.append(g)
    return goals
```

## Dependencies

- F-04 (`inner_goals` table — `id`, `title`, `tags`, `last_progress_at`, `completed`, `outcome`).
- D-03 (`CandidateAction` dataclass defined in `candidates.py`).

## Testing

### Unit tests — `tests/unit/test_goal_candidates.py`

```python
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
```

## Acceptance criteria

- A "playlist" goal → `CandidateAction(tool="spotify", verb="build_playlist")`.
- A stale goal (no progress in 14+ days) → `fail_stale_goal()` called, goal excluded from candidates.
- Completed goals excluded.
- Each candidate has `source="goal"` and `source_id` matching the goal's `id`.
