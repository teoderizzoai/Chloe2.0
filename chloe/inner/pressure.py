"""Pressure dynamics for inner_wants / inner_fears / inner_tensions.

Pressure is the scalar that drives initiative candidates. Items decay over
time (exponential) but escalate when unresolved past a threshold age.

Decay half-lives are intentionally long: a want shouldn't evaporate after a
day. The escalation curve is what makes long-unresolved tensions eventually
demand attention.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Iterable

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection

log = get_logger("inner.pressure")

# Half-life in days. Longer for fears (they linger), shorter for tensions
# (they either resolve or escalate).
HALF_LIFE_DAYS = {
    "want": 7.0,
    "fear": 14.0,
    "tension": 4.0,
}

ESCALATION_AGE_DAYS = 5.0
ESCALATION_RATE_PER_DAY = 0.04
MIN_PRESSURE_TO_KEEP = 0.05


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _decay(pressure: float, age_days: float, half_life: float) -> float:
    return pressure * math.pow(0.5, max(0.0, age_days) / half_life)


def _escalation(age_days: float) -> float:
    if age_days <= ESCALATION_AGE_DAYS:
        return 0.0
    return min(0.4, (age_days - ESCALATION_AGE_DAYS) * ESCALATION_RATE_PER_DAY)


def _sweep_table(table: str, kind: str) -> dict:
    conn = get_connection()
    rows = conn.execute(
        f"SELECT id, pressure, created_at, resolved FROM {table}"
    ).fetchall()
    half_life = HALF_LIFE_DAYS[kind]
    now = _now()
    decayed = 0
    pruned = 0
    escalated = 0

    for row in rows:
        if row["resolved"]:
            continue
        created = _parse_ts(row["created_at"])
        if created is None:
            continue
        age_days = (now - created).total_seconds() / 86400.0
        new_pressure = _decay(row["pressure"], age_days, half_life)
        bump = _escalation(age_days)
        if bump > 0:
            new_pressure = min(1.0, new_pressure + bump)
            escalated += 1

        if new_pressure < MIN_PRESSURE_TO_KEEP:
            conn.execute(f"UPDATE {table} SET resolved=1, pressure=0.0 WHERE id=?", (row["id"],))
            pruned += 1
        elif abs(new_pressure - row["pressure"]) > 0.001:
            conn.execute(f"UPDATE {table} SET pressure=? WHERE id=?", (new_pressure, row["id"]))
            decayed += 1

    conn.commit()
    return {"decayed": decayed, "escalated": escalated, "pruned": pruned}


def decay_all_pressures() -> dict:
    """Sweep all three pressure tables. Safe to call frequently — uses age, not last-decay-time."""
    stats = {
        "wants": _sweep_table("inner_wants", "want"),
        "fears": _sweep_table("inner_fears", "fear"),
        "tensions": _sweep_table("inner_tensions", "tension"),
    }
    log.info("pressure_sweep", **{f"{k}_{ik}": iv for k, sv in stats.items() for ik, iv in sv.items()})
    return stats


def add_want(text: str, tags: Iterable[str] = (), pressure: float = 0.6, subtype: str | None = None) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO inner_wants (text, tags, pressure, subtype, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (text, json.dumps(list(tags)), max(0.0, min(1.0, pressure)), subtype, _now().isoformat()),
    )
    conn.commit()
    log.info("want_added", id=cur.lastrowid, pressure=pressure, tags=list(tags))
    return cur.lastrowid


def add_fear(text: str, tags: Iterable[str] = (), pressure: float = 0.6) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO inner_fears (text, tags, pressure, created_at)
           VALUES (?, ?, ?, ?)""",
        (text, json.dumps(list(tags)), max(0.0, min(1.0, pressure)), _now().isoformat()),
    )
    conn.commit()
    log.info("fear_added", id=cur.lastrowid, pressure=pressure)
    return cur.lastrowid


def add_tension(text: str, tags: Iterable[str] = (), pressure: float = 0.65) -> int:
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO inner_tensions (text, tags, pressure, created_at)
           VALUES (?, ?, ?, ?)""",
        (text, json.dumps(list(tags)), max(0.0, min(1.0, pressure)), _now().isoformat()),
    )
    conn.commit()
    log.info("tension_added", id=cur.lastrowid, pressure=pressure)
    return cur.lastrowid


def resolve(table: str, item_id: int, note: str | None = None) -> None:
    if table not in {"inner_wants", "inner_fears", "inner_tensions"}:
        raise ValueError(f"unknown table: {table}")
    conn = get_connection()
    conn.execute(f"UPDATE {table} SET resolved=1, pressure=0.0 WHERE id=?", (item_id,))
    conn.commit()
    log.info("inner_resolved", table=table, id=item_id, note=note)


def boost_pressure(table: str, item_id: int, delta: float) -> None:
    if table not in {"inner_wants", "inner_fears", "inner_tensions"}:
        raise ValueError(f"unknown table: {table}")
    conn = get_connection()
    conn.execute(
        f"UPDATE {table} SET pressure = MIN(1.0, MAX(0.0, pressure + ?)) WHERE id=?",
        (delta, item_id),
    )
    conn.commit()
