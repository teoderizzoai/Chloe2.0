from datetime import datetime

from fastapi import APIRouter

from chloe.state.kv import get as kv_get

router = APIRouter(prefix="/admin/shadow", tags=["admin"])


@router.get("")
async def shadow_comparison():
    """Side-by-side comparison of old vs new engine decisions."""
    records = kv_get("shadow_decisions", default=[])

    total = len(records)
    new_idle = sum(1 for r in records if r.get("was_idle"))
    new_proposed = total - new_idle

    by_tool: dict = {}
    for r in records:
        if r.get("proposed"):
            tool = r["proposed"].get("tool", "unknown")
            by_tool[tool] = by_tool.get(tool, 0) + 1

    recent = records[-50:]

    return {
        "summary": {
            "total_ticks": total,
            "new_engine_idle": new_idle,
            "new_engine_proposed": new_proposed,
            "idle_rate": round(new_idle / total, 3) if total else 0,
            "by_tool": by_tool,
        },
        "recent_decisions": recent,
        "generated_at": datetime.utcnow().isoformat(),
    }
