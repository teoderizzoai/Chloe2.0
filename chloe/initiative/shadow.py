from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

from chloe.initiative.engine import tick as new_tick
from chloe.state.kv import get as kv_get, set as kv_set
from chloe.observability.logging import get_logger

log = get_logger("initiative.shadow")

MAX_SHADOW_RECORDS = 2000


async def shadow_tick() -> None:
    """
    Run the new initiative engine in shadow mode.
    Logs what the new engine WOULD have done without actually calling gate.submit.
    """
    shadow_gate_calls = []

    async def shadow_gate(action):
        shadow_gate_calls.append({
            "tool": action.tool,
            "verb": action.verb,
            "intent": action.intent,
            "authorization": action.authorization,
        })
        from chloe.actions.schema import ActionResult
        return ActionResult(executed=False, suppressed=False)

    try:
        with patch("chloe.initiative.engine.gate_submit", new=shadow_gate):
            await new_tick()
    except Exception as exc:
        log.warning("shadow_tick_error", error=str(exc))
        return

    shadow_decision = {
        "timestamp": datetime.utcnow().isoformat(),
        "proposed": shadow_gate_calls[0] if shadow_gate_calls else None,
        "was_idle": len(shadow_gate_calls) == 0,
    }

    records = kv_get("shadow_decisions", default=[])
    records.append(shadow_decision)
    records = records[-MAX_SHADOW_RECORDS:]
    kv_set("shadow_decisions", records)

    if shadow_decision["proposed"]:
        log.info("shadow_would_have",
                 tool=shadow_decision["proposed"]["tool"],
                 verb=shadow_decision["proposed"]["verb"])
    else:
        log.debug("shadow_idle")
