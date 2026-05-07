from __future__ import annotations

import json
from datetime import datetime, timedelta

from chloe.observability.logging import get_logger

log = get_logger("traits")

HUMOR_SEED_THRESHOLD = 4
HUMOR_SEED_WINDOW_DAYS = 14

HUMOR_KIND_TO_TRAIT = {
    "dry": "finds dry wit charming",
    "warm": "responds to warmth with warmth",
    "playful": "enjoys being teased and teasing back",
    "dark": "comfortable with dark humor",
    "absurdist": "delights in absurdist tangents",
}


def record_humor_detection(kind: str | None, direction: str | None) -> None:
    """
    Increment humor detection counter for the given kind.
    Seeds a candidate trait when threshold is reached within the window.
    """
    if kind is None:
        return

    from chloe.state import kv as kv_mod

    key = f"humor_detections_{kind}"
    records = kv_mod.get(key) or []
    if not isinstance(records, list):
        records = []

    now = datetime.utcnow().isoformat()
    cutoff = (datetime.utcnow() - timedelta(days=HUMOR_SEED_WINDOW_DAYS)).isoformat()

    records.append(now)
    records = [r for r in records if r >= cutoff]
    kv_mod.set(key, records)

    if len(records) >= HUMOR_SEED_THRESHOLD:
        _seed_humor_trait_if_absent(kind)


def _seed_humor_trait_if_absent(kind: str) -> None:
    from chloe.state.db import get_connection

    trait_name = HUMOR_KIND_TO_TRAIT.get(kind)
    if trait_name is None:
        return

    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM identity_traits WHERE name = ?", (trait_name,)
    ).fetchone()
    if existing:
        return

    conn.execute(
        """
        INSERT INTO identity_traits (name, weight, status, created_at, updated_at)
        VALUES (?, 0.3, 'emerging', datetime('now'), datetime('now'))
        """,
        (trait_name,),
    )
    conn.commit()
    log.info("humor_trait_seeded", kind=kind, name=trait_name)
