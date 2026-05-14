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
    """Pressure decay, vitals, and affect tick — sweeps every 10 minutes."""
    from chloe.inner.pressure import decay_all_pressures
    from chloe.affect.vitals import tick_vitals, log_snapshot
    while True:
        try:
            decay_all_pressures()
            tick_vitals()
            _tick_affect_dims()
            log_snapshot()
        except Exception as e:
            log.warning("pressure_decay_error", error=str(e))
        await asyncio.sleep(600)


def _tick_affect_dims() -> None:
    """Advance affect dimensions based on time-of-day and recent activity residue."""
    try:
        from chloe.affect.dims import load as load_affect, save as save_affect, tick as affect_tick
        from chloe.inner.residue import compute_residue
        from chloe.state.kv import get as kv_get
        state = load_affect()
        residue = compute_residue()
        last_chat = kv_get("last_chat_seen")
        new_state = affect_tick(state, residue=residue, last_chat_seen=last_chat)
        save_affect(new_state)
        log.debug("affect_dims_ticked",
                  valence=round(new_state.valence, 3),
                  arousal=round(new_state.arousal, 3),
                  social_pull=round(new_state.social_pull, 3))
    except Exception as exc:
        log.warning("affect_dims_tick_error", error=str(exc))


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
