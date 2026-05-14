"""Vitals tick — energy restore/drain cycle.

Called from pressure_loop every 10 minutes. Energy fills during the sleep
window (from quiet_hours prefs) and drains while awake. Initiative actions
consume extra energy on top of the passive drain via consume_energy().

Calibration (per 10-min tick):
  Sleep:  +0.014  → 8 h × 6 ticks = 48 ticks → +0.67 from near-empty
  Wake:   -0.005  → 16 h × 6 ticks = 96 ticks → -0.48 passive drain
  Action: -0.04   → ~5 actions/day → -0.20 extra
  Net daily drain ≈ 0.68, restored ≈ 0.67 → sustainable daily cycle
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from chloe.affect.dims import load as load_affect, save as save_affect
from chloe.observability.logging import get_logger

log = get_logger("affect.vitals")

_SLEEP_RESTORE_PER_TICK = 0.014
_WAKE_DRAIN_PER_TICK = 0.005
_ACTION_ENERGY_COST = 0.04


def tick_vitals(now: datetime | None = None) -> None:
    """Update energy for the current 10-minute period."""
    if now is None:
        now = datetime.now(timezone.utc)

    sleeping = is_sleep_window(now)
    state = load_affect()

    if sleeping:
        state.energy = min(1.0, state.energy + _SLEEP_RESTORE_PER_TICK)
        state.depletion = max(0.0, state.depletion - 0.01)
    else:
        state.energy = max(0.0, state.energy - _WAKE_DRAIN_PER_TICK)

    save_affect(state)
    log.debug("vitals_tick", sleeping=sleeping, energy=round(state.energy, 3),
              depletion=round(state.depletion, 3))


def consume_energy(cost: float = _ACTION_ENERGY_COST) -> None:
    """Deduct energy after an initiative action executes."""
    state = load_affect()
    before = state.energy
    state.energy = max(0.0, state.energy - cost)
    save_affect(state)
    log.debug("energy_consumed", cost=cost, before=round(before, 3),
              after=round(state.energy, 3))


def log_snapshot() -> None:
    """Write the current affect + vitals to vitals_log. Called after each pressure tick."""
    try:
        from chloe.state.db import get_connection
        state = load_affect()
        conn = get_connection()
        conn.execute(
            "INSERT INTO vitals_log (energy, valence, arousal, social_pull, openness) VALUES (?,?,?,?,?)",
            (round(state.energy, 4), round(state.valence, 4),
             round(state.arousal, 4), round(state.social_pull, 4), round(state.openness, 4)),
        )
        conn.commit()
    except Exception as exc:
        log.debug("vitals_log_failed", error=str(exc))


def is_sleep_window(now: datetime) -> bool:
    """True if the current UTC time falls within the configured sleep window."""
    try:
        from chloe.state.db import get_connection
        from chloe.actions.leash import _in_quiet_hours
        conn = get_connection()
        row = conn.execute("SELECT value FROM preferences WHERE key='quiet_hours'").fetchone()
        quiet_config = json.loads(row["value"]) if row else {}
        return _in_quiet_hours(now, quiet_config)
    except Exception:
        h = now.hour
        return h >= 23 or h < 7
