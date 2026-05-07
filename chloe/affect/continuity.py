from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from chloe.observability.logging import get_logger
from chloe.state.kv import get as kv_get, set as kv_set

log = get_logger("affect.continuity")

CHECKPOINT_KEY       = "affect_checkpoint"
VALENCE_HALF_LIFE_H  = 6.0
AROUSAL_HALF_LIFE_H  = 2.0
AROUSAL_BASELINE     = 0.2
ABSENCE_THRESHOLD_H  = 48
ABSENCE_VALENCE_DRIFT = -0.10
GOAL_DONE_VALENCE    = +0.15
GOAL_DONE_AROUSAL    = +0.10


def save_checkpoint(valence: float, arousal: float, label: str) -> None:
    state = {
        "valence": round(valence, 4),
        "arousal": round(arousal, 4),
        "label": label,
        "saved_at": datetime.utcnow().isoformat(),
    }
    kv_set(CHECKPOINT_KEY, json.dumps(state))
    log.debug("affect_checkpoint_saved", valence=valence, arousal=arousal, label=label)


def restore_checkpoint() -> dict:
    raw = kv_get(CHECKPOINT_KEY)
    if not raw:
        return _neutral()

    try:
        state = json.loads(raw)
    except Exception:
        return _neutral()

    saved_at = _parse_dt(state.get("saved_at", ""))
    if saved_at is None:
        return _neutral()

    elapsed_h = (datetime.utcnow() - saved_at).total_seconds() / 3600.0

    decayed_valence = _decay(state.get("valence", 0.0), 0.0, elapsed_h, VALENCE_HALF_LIFE_H)
    decayed_arousal = _decay(state.get("arousal", AROUSAL_BASELINE), AROUSAL_BASELINE, elapsed_h, AROUSAL_HALF_LIFE_H)
    decayed_valence = _apply_absence_penalty(decayed_valence, elapsed_h)
    label = _rederive_label(decayed_valence, decayed_arousal)

    log.info(
        "affect_checkpoint_restored",
        elapsed_h=round(elapsed_h, 1),
        valence=round(decayed_valence, 3),
        arousal=round(decayed_arousal, 3),
        label=label,
    )
    return {"valence": decayed_valence, "arousal": decayed_arousal, "label": label}


def apply_goal_completion_pulse(current_valence: float, current_arousal: float) -> tuple[float, float]:
    new_valence = min(1.0, current_valence + GOAL_DONE_VALENCE)
    new_arousal = min(1.0, current_arousal + GOAL_DONE_AROUSAL)
    log.info("goal_completion_pulse", delta_v=GOAL_DONE_VALENCE, delta_a=GOAL_DONE_AROUSAL)
    return new_valence, new_arousal


def _decay(current: float, target: float, elapsed_h: float, half_life_h: float) -> float:
    if elapsed_h <= 0:
        return current
    k = math.log(2) / half_life_h
    decayed = target + (current - target) * math.exp(-k * elapsed_h)
    return round(max(-1.0, min(1.0, decayed)), 4)


def _apply_absence_penalty(valence: float, elapsed_h: float) -> float:
    if elapsed_h < ABSENCE_THRESHOLD_H:
        return valence
    excess_h = elapsed_h - ABSENCE_THRESHOLD_H
    penalty = ABSENCE_VALENCE_DRIFT * min(1.0, excess_h / 24.0)
    result = max(-0.5, valence + penalty)
    log.debug("absence_penalty_applied", elapsed_h=elapsed_h, penalty=round(penalty, 3))
    return result


def _rederive_label(valence: float, arousal: float) -> str:
    try:
        from chloe.affect.label import label_from_dims
        return label_from_dims(valence, arousal)
    except Exception:
        if valence > 0.3:
            return "content"
        elif valence < -0.3:
            return "low"
        return "neutral"


def _neutral() -> dict:
    return {"valence": 0.0, "arousal": AROUSAL_BASELINE, "label": "neutral"}


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None
