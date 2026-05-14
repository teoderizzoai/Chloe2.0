from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timezone

from chloe.state.db import get_connection
from chloe.observability.logging import get_logger

log = get_logger("affect.dims")

HALF_LIVES: dict[str, int] = {
    "episodic": 60,
    "semantic": 180,
    "autobiographical": 365,
    "procedural": 90,
}


@dataclass
class AffectState:
    valence: float = 0.0      # [-1, 1]
    arousal: float = 0.4      # [0, 1]
    social_pull: float = 0.5  # [0, 1]
    openness: float = 0.6     # [0, 1]
    depletion: float = 0.0    # [0, 1] — accumulates from intensive conversations, slow decay
    energy: float = 0.8       # [0, 1] — initiative fuel; restored by sleep, consumed by actions

    def clamp(self) -> "AffectState":
        self.valence = max(-1.0, min(1.0, self.valence))
        self.arousal = max(0.0, min(1.0, self.arousal))
        self.social_pull = max(0.0, min(1.0, self.social_pull))
        self.openness = max(0.0, min(1.0, self.openness))
        self.depletion = max(0.0, min(1.0, self.depletion))
        self.energy = max(0.0, min(1.0, self.energy))
        return self


def tick(
    state: AffectState,
    vitals: dict | None = None,
    hour: int | None = None,
    recent_records: list | None = None,
    last_chat_seen: str | None = None,
) -> AffectState:
    if random.random() < 0.05:
        return state

    hour = hour if hour is not None else datetime.now().hour
    records = recent_records or []

    v = state.valence
    a = state.arousal
    sp = state.social_pull
    op = state.openness

    # Time-of-day dynamics
    if 6 <= hour < 12:
        a += 0.02
        op += 0.01
    elif 18 <= hour < 22:
        sp += 0.01
    elif hour >= 22 or hour < 6:
        a -= 0.02
        sp -= 0.01

    # Residue from recent affect records
    for rec in records:
        vd = getattr(rec, "valence_delta", None)
        ad = getattr(rec, "arousal_delta", None)
        res = getattr(rec, "residue", 0.0)
        if vd is not None:
            v += vd * res
        if ad is not None:
            a += ad * res

    # Mean-reversion toward baseline
    v += (0.0 - v) * 0.02
    a += (0.4 - a) * 0.02
    sp += (0.5 - sp) * 0.01
    op += (0.6 - op) * 0.01
    # Depletion decays with ~12-24h half-life (much slower than arousal's ~2h)
    dp = state.depletion + (0.0 - state.depletion) * 0.003

    # Recent chat boosts social pull
    if last_chat_seen:
        try:
            last = datetime.fromisoformat(last_chat_seen.replace("Z", "+00:00"))
            hours_ago = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if hours_ago < 2:
                sp += 0.05
        except Exception:
            pass

    return AffectState(valence=v, arousal=a, social_pull=sp, openness=op, depletion=dp).clamp()


def load() -> AffectState:
    conn = get_connection()
    row = conn.execute(
        "SELECT valence, arousal, social_pull, openness, depletion, energy FROM affect_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return AffectState()
    return AffectState(
        valence=row["valence"],
        arousal=row["arousal"],
        social_pull=row["social_pull"],
        openness=row["openness"],
        depletion=float(row["depletion"] or 0.0),
        energy=float(row["energy"] if row["energy"] is not None else 0.8),
    )


def save(state: AffectState) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO affect_state (id, valence, arousal, social_pull, openness, depletion, energy, updated_at)
        VALUES (1, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            valence = excluded.valence,
            arousal = excluded.arousal,
            social_pull = excluded.social_pull,
            openness = excluded.openness,
            depletion = excluded.depletion,
            energy = excluded.energy,
            updated_at = excluded.updated_at
        """,
        (
            state.valence,
            state.arousal,
            state.social_pull,
            state.openness,
            state.depletion,
            state.energy,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


_FELT_STATE_CACHE: dict = {"phrase": "", "valence": None, "arousal": None, "social_pull": None, "openness": None}
_FELT_STATE_CHANGE_THRESHOLD = 0.15


def felt_state_phrase() -> str:
    """Return a cached felt-state phrase in Chloe's voice.

    Regenerated lazily when any affect dimension changes > 0.15. The phrase
    avoids abstract psychological labels — only physical or spatial texture.
    Result is stored in kv so it survives across coroutines.
    """
    from chloe.state.kv import get as kv_get, set as kv_set

    current = load()

    cached = kv_get("affect:felt_state") or {}
    if isinstance(cached, dict) and cached.get("phrase"):
        prev_v = float(cached.get("valence") or 0.0)
        prev_a = float(cached.get("arousal") or 0.4)
        drift = max(
            abs(current.valence - prev_v),
            abs(current.arousal - prev_a),
        )
        if drift < _FELT_STATE_CHANGE_THRESHOLD:
            return cached["phrase"]

    # Generate synchronously using a simple heuristic first, then update async
    phrase = _heuristic_felt_state(current)
    kv_set("affect:felt_state", {
        "phrase": phrase,
        "valence": current.valence,
        "arousal": current.arousal,
    })
    return phrase


def _heuristic_felt_state(state: AffectState) -> str:
    """Fast deterministic felt-state phrase from affect dimensions.

    No LLM call — used for latency-sensitive chat path. The async generation
    (Step 24 intent) would run as a background update, but the heuristic is
    good enough for most turns and avoids adding another LLM hop.
    """
    v = state.valence
    a = state.arousal
    sp = state.social_pull

    if state.energy < 0.2:
        return "somewhere between rest and absence, not fully back yet"
    if v > 0.5 and a > 0.6:
        return "something moving faster than usual, in a good way"
    if v > 0.3 and sp > 0.6:
        return "open, a bit like a door left ajar"
    if v < -0.4 and a < 0.3:
        return "quieter than normal, the kind that doesn't announce itself"
    if v < -0.3 and a > 0.5:
        return "something running close to the surface, not sure what it wants"
    if a < 0.2:
        return "still, the way a room feels after everyone leaves"
    if a > 0.7 and sp < 0.35:
        return "activated but not sure where to put it"
    if sp > 0.7:
        return "drawn toward something, not just anyone"
    if -0.1 <= v <= 0.1 and 0.3 <= a <= 0.6:
        return "steady, nothing pushing hard in any direction"
    return "present, more or less"


def tone_block(affect: AffectState) -> str:
    """Map 4D affect to grounded texture phrases, not abstract psychological labels.

    These read as internal state texture the LLM reasons from, rather than
    directives that tell it what to do. The phrases leave room for expression
    rather than prescribing tone.
    """
    lines: list[str] = []

    if affect.valence > 0.3:
        lines.append("Something lighter than usual — things landing well today.")
    elif affect.valence < -0.3:
        lines.append("Carrying something; not heavy, just present and not yet put down.")

    if affect.energy < 0.2:
        lines.append("Somewhere between rest and absence — not quite back yet, running on fumes.")
    elif affect.arousal > 0.7:
        lines.append("Moving faster inside than out — attention sharpened, things clicking.")
    elif affect.arousal < 0.2:
        if affect.depletion > 0.4:
            lines.append("Something heavier than quiet — not at rest, just run down.")
        else:
            lines.append("Slow and unhurried; no urgency in how she's sitting with things.")

    if affect.social_pull > 0.7:
        lines.append("Pulled toward connection — not just open, actively reaching.")
    elif affect.social_pull < 0.3:
        lines.append("Needing quiet more than company right now.")

    if affect.openness > 0.75:
        lines.append("Unusually open — willing to go somewhere vulnerable if it arises.")
    elif affect.openness < 0.35:
        lines.append("More guarded than usual; closer to the surface, less inclined to go deep.")

    if not lines:
        lines.append("Steady; nothing pushing hard in any direction.")

    return "\n".join(lines[:4])
