"""Goal helpers — progress, completion, failure, listing.

Goals are first-class state surfaced to the user via the dashboard. Progress
is computed from action history (the `last_action_at` field), not self-report.
"""
from __future__ import annotations

from datetime import datetime, timezone

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("identity.goals")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_goal(name: str, why: str | None = None, target_artifact_ref: str | None = None) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO inner_goals (name, why, target_artifact_ref, status, created_at)
           VALUES (?, ?, ?, 'active', ?)""",
        (name, why, target_artifact_ref, _now()),
    )
    conn.commit()
    log.info("goal_added", id=cur.lastrowid, name=name)
    return cur.lastrowid


def update_progress(goal_id: int, delta: float, note: str | None = None) -> float | None:
    conn = get_connection()
    row = conn.execute("SELECT progress, status FROM inner_goals WHERE id=?", (goal_id,)).fetchone()
    if not row:
        log.warning("goal_progress_not_found", goal_id=goal_id)
        return None
    if row["status"] in {"done", "failed", "stale"}:
        return row["progress"]

    new_p = max(0.0, min(1.0, row["progress"] + delta))
    new_status = "done" if new_p >= 1.0 else row["status"]
    conn.execute(
        """UPDATE inner_goals SET progress=?, status=?, last_action_at=? WHERE id=?""",
        (new_p, new_status, _now(), goal_id),
    )
    conn.commit()
    log.info("goal_progress", goal_id=goal_id, delta=delta, progress=new_p, note=note)

    if new_status == "done":
        _write_completion_memory(goal_id)
    return new_p


def complete_goal(goal_id: int) -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE inner_goals SET status='done', progress=1.0, last_action_at=? WHERE id=?",
        (_now(), goal_id),
    )
    conn.commit()
    _write_completion_memory(goal_id)
    log.info("goal_completed", goal_id=goal_id)


def fail_goal(goal_id: int, reason: str = "") -> None:
    conn = get_connection()
    conn.execute(
        "UPDATE inner_goals SET status='failed', last_action_at=? WHERE id=?",
        (_now(), goal_id),
    )
    conn.commit()
    log.info("goal_failed", goal_id=goal_id, reason=reason)


def mark_action(goal_id: int) -> None:
    """Touch last_action_at to keep the goal from going stale."""
    conn = get_connection()
    conn.execute("UPDATE inner_goals SET last_action_at=? WHERE id=?", (_now(), goal_id))
    conn.commit()


def active_goals() -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, name, why, progress, status, created_at, last_action_at, deadline
           FROM inner_goals
           WHERE status NOT IN ('done', 'failed', 'stale')
           ORDER BY created_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def _write_completion_memory(goal_id: int) -> None:
    conn = get_connection()
    row = conn.execute("SELECT name FROM inner_goals WHERE id=?", (goal_id,)).fetchone()
    if not row:
        return
    try:
        from chloe.memory.store import add as memory_add
        memory_add(
            kind="autobiographical",
            text=f"Finished a goal: {row['name']}",
            source="goal_complete",
            source_ref=str(goal_id),
            tags=["goal", "completed"],
            weight=0.7,
            salience=0.7,
        )
    except Exception as exc:
        log.warning("goal_completion_memory_failed", error=str(exc), goal_id=goal_id)
