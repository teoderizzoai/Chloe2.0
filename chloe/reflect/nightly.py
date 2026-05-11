"""Nightly reflect — orchestrates sleep consolidation, decay, and pruning.

Runs once per night (~03:00 local). Composes existing pieces:
- memory consolidation (cluster recent episodics → semantic summaries)
- pressure sweep (decay + escalation across inner_*)
- interest garden daily decay + low-interest archival
"""
from __future__ import annotations

from chloe.observability.logging import get_logger

log = get_logger("reflect.nightly")


async def run_nightly() -> dict:
    """Run all nightly jobs sequentially. Returns aggregated stats."""
    log.info("nightly_start")

    out: dict = {}

    try:
        from chloe.memory.consolidation import consolidate_sleep
        out["consolidation"] = await consolidate_sleep()
    except Exception as exc:
        log.warning("nightly_consolidation_error", error=str(exc))
        out["consolidation"] = {"error": str(exc)}

    try:
        from chloe.inner.pressure import decay_all_pressures
        out["pressure"] = decay_all_pressures()
    except Exception as exc:
        log.warning("nightly_pressure_error", error=str(exc))
        out["pressure"] = {"error": str(exc)}

    try:
        from chloe.identity.interest_garden import decay_interests, archive_low_interests
        decayed = decay_interests()
        archived = archive_low_interests()
        out["interest_garden"] = {"decayed": decayed, "archived": archived}
    except Exception as exc:
        log.warning("nightly_interest_error", error=str(exc))
        out["interest_garden"] = {"error": str(exc)}

    log.info("nightly_complete", **{f"{k}_keys": list(v.keys()) for k, v in out.items() if isinstance(v, dict)})
    return out
