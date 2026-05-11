"""Weekly reflect — orchestrates procedural distillation + weekly self-modeling.

Runs Sunday ~03:00 local. Wraps two existing capable pieces:
- memory.procedural.distill_procedural — Flash, last 7 days of feedback pairs
- identity.self_model.run_weekly_self_model — Pro thinking, ~$0.08/week
"""
from __future__ import annotations

from chloe.observability.logging import get_logger

log = get_logger("reflect.weekly")


async def run_weekly() -> dict:
    log.info("weekly_start")
    out: dict = {}

    try:
        from chloe.memory.procedural import distill_procedural
        new_ids = await distill_procedural()
        out["procedural"] = {"new_rules": len(new_ids), "ids": new_ids}
    except Exception as exc:
        log.warning("weekly_procedural_error", error=str(exc))
        out["procedural"] = {"error": str(exc)}

    try:
        from chloe.identity.self_model import run_weekly_self_model
        result = await run_weekly_self_model()
        out["self_model"] = result or {"error": "no_result"}
    except Exception as exc:
        log.warning("weekly_self_model_error", error=str(exc))
        out["self_model"] = {"error": str(exc)}

    log.info("weekly_complete")
    return out
