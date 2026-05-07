import asyncio
from datetime import datetime

from chloe.tools.messages import MessagesTool
from chloe.tools.smart_home import SmartHomeTool
from chloe.tools.registry import get_registry
from chloe.observability.logging import get_logger

log = get_logger("loop")


def register_all_tools(discord_send_callback=None):
    registry = get_registry()
    registry.register(MessagesTool(send_callback=discord_send_callback))
    registry.register(SmartHomeTool())


async def initiative_loop():
    """Main initiative loop — runs tick() every 60 seconds."""
    from chloe.initiative.engine import tick as initiative_tick
    while True:
        try:
            await initiative_tick()
        except Exception as e:
            log.warning("initiative_tick_error", error=str(e))
        await asyncio.sleep(60)


async def daily_job_loop():
    """Polls every 5 minutes; runs daily retention job at 04:30."""
    while True:
        try:
            await _run_daily_jobs()
        except Exception as exc:
            log.warning("daily_jobs_error", error=str(exc))
        await asyncio.sleep(300)


async def weekly_job_loop():
    """Polls hourly; runs weekly jobs on Sunday at 03:00."""
    while True:
        try:
            now = datetime.now()
            if now.weekday() == 6 and now.hour == 3:
                await _run_weekly_jobs()
        except Exception as exc:
            log.warning("weekly_jobs_error", error=str(exc))
        await asyncio.sleep(3600)


async def _run_daily_jobs():
    from chloe.memory.retention import run_retention_job

    now = datetime.now()
    if now.hour == 4 and now.minute >= 30:
        log.info("daily_retention_start")
        await run_retention_job()


async def _run_weekly_jobs():
    from chloe.memory.procedural import distill_procedural
    from chloe.identity.self_model import run_weekly_self_model

    log.info("weekly_jobs_start")
    await distill_procedural()
    await run_weekly_self_model()
    log.info("weekly_jobs_complete")
