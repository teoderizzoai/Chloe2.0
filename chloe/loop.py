import asyncio
from datetime import datetime

from chloe.tools.messages import MessagesTool
from chloe.tools.smart_home import SmartHomeTool
from chloe.tools.registry import get_registry
from chloe.observability.logging import get_logger

log = get_logger("loop")


def register_all_tools(discord_send_callback=None):
    from chloe.tools.weather import WeatherTool
    from chloe.tools.maps import MapsTool
    from chloe.tools.code_runner import CodeRunnerTool
    from chloe.tools.spotify import SpotifyTool
    from chloe.tools.calendar import CalendarTool
    from chloe.tools.reminders import RemindersTool
    from chloe.tools.notes import NotesTool
    from chloe.tools.gmail import GmailTool
    from chloe.tools.fs_workspace import FsWorkspaceTool
    from chloe.tools.web_search import WebSearchTool
    from chloe.tools.self_tools import SelfToolsTool

    registry = get_registry()
    registry.register(MessagesTool(send_callback=discord_send_callback))
    registry.register(SmartHomeTool())
    registry.register(WeatherTool())
    registry.register(MapsTool())
    registry.register(CodeRunnerTool())
    registry.register(SpotifyTool())
    registry.register(CalendarTool())
    registry.register(RemindersTool())
    registry.register(NotesTool())
    registry.register(GmailTool())
    registry.register(FsWorkspaceTool())
    registry.register(WebSearchTool())
    registry.register(SelfToolsTool())


async def initiative_loop():
    """Main initiative loop — runs tick() every 60 seconds."""
    from chloe.initiative.engine import tick as initiative_tick
    while True:
        try:
            await initiative_tick()
        except Exception as e:
            log.warning("initiative_tick_error", error=str(e))
        await asyncio.sleep(60)


async def reflect_loop():
    """Reflect loop — runs the every-2h pass when due."""
    from chloe.reflect.every_2h import run_reflect
    while True:
        try:
            await run_reflect()
        except Exception as e:
            log.warning("reflect_tick_error", error=str(e))
        await asyncio.sleep(300)


async def pressure_loop():
    """Pressure decay and vitals loop — sweeps every 10 minutes."""
    from chloe.inner.pressure import decay_all_pressures
    from chloe.affect.vitals import tick_vitals
    while True:
        try:
            decay_all_pressures()
            tick_vitals()
        except Exception as e:
            log.warning("pressure_decay_error", error=str(e))
        await asyncio.sleep(600)


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
    from chloe.reflect.nightly import run_nightly

    now = datetime.now()
    if now.hour == 3 and now.minute < 5:
        log.info("nightly_jobs_start")
        await run_nightly()
    if now.hour == 4 and now.minute >= 30:
        log.info("daily_retention_start")
        await run_retention_job()


async def _run_weekly_jobs():
    from chloe.reflect.weekly import run_weekly

    log.info("weekly_jobs_start")
    await run_weekly()
    log.info("weekly_jobs_complete")
