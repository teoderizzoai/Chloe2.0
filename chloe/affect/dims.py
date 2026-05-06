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

    def clamp(self) -> "AffectState":
        self.valence = max(-1.0, min(1.0, self.valence))
        self.arousal = max(0.0, min(1.0, self.arousal))
        self.social_pull = max(0.0, min(1.0, self.social_pull))
        self.openness = max(0.0, min(1.0, self.openness))
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

    # Recent chat boosts social pull
    if last_chat_seen:
        try:
            last = datetime.fromisoformat(last_chat_seen.replace("Z", "+00:00"))
            hours_ago = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            if hours_ago < 2:
                sp += 0.05
        except Exception:
            pass

    return AffectState(valence=v, arousal=a, social_pull=sp, openness=op).clamp()


def load() -> AffectState:
    conn = get_connection()
    row = conn.execute(
        "SELECT valence, arousal, social_pull, openness FROM affect_state WHERE id = 1"
    ).fetchone()
    if row is None:
        return AffectState()
    return AffectState(
        valence=row["valence"],
        arousal=row["arousal"],
        social_pull=row["social_pull"],
        openness=row["openness"],
    )


def save(state: AffectState) -> None:
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO affect_state (id, valence, arousal, social_pull, openness, updated_at)
        VALUES (1, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            valence = excluded.valence,
            arousal = excluded.arousal,
            social_pull = excluded.social_pull,
            openness = excluded.openness,
            updated_at = excluded.updated_at
        """,
        (
            state.valence,
            state.arousal,
            state.social_pull,
            state.openness,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


def tone_block(affect: AffectState) -> str:
    """Map 4D affect dimensions to a 1-3 line tone hint for the system prompt."""
    lines: list[str] = []

    if affect.valence > 0.3:
        lines.append("Her tone is warm and optimistic.")
    elif affect.valence < -0.3:
        lines.append("Her tone is subdued and introspective.")

    if affect.arousal > 0.7:
        lines.append("She feels energized and engaged.")
    elif affect.arousal < 0.2:
        lines.append("She feels calm and unhurried.")

    if affect.social_pull > 0.7:
        lines.append("She is particularly drawn toward connection right now.")
    elif affect.social_pull < 0.3:
        lines.append("She needs some space and quiet today.")

    if not lines:
        lines.append("Her affect is balanced and steady.")

    return "\n".join(lines[:3])
