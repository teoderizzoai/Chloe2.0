from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from chloe.admin.api import admin_router
from chloe.admin.shadow_routes import router as shadow_router
from chloe.channels.confirm_routes import devices_router, router as confirm_router
from chloe.channels.ha_prefs_routes import router as ha_prefs_router
from chloe.channels.mobile_routes import router as mobile_router
from chloe.channels.revert_routes import router as revert_router
from chloe.observability.logging import get_logger
from chloe.observability.metrics import metrics_router

log = get_logger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    from chloe.state.db import migrate
    from chloe.loop import register_all_tools, initiative_loop, daily_job_loop, weekly_job_loop

    log.info("startup_begin")

    applied = migrate()
    log.info("migrations_applied", count=applied)

    register_all_tools()

    tasks = [
        asyncio.create_task(initiative_loop(), name="initiative"),
        asyncio.create_task(daily_job_loop(),  name="daily_jobs"),
        asyncio.create_task(weekly_job_loop(), name="weekly_jobs"),
    ]
    log.info("background_loops_started")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    from chloe.state.db import close as db_close
    db_close()
    log.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(title="Chloe 2.0", lifespan=lifespan)

    @app.get("/")
    async def health():
        return {"status": "ok"}

    app.include_router(metrics_router)
    app.include_router(admin_router,   prefix="/admin")
    app.include_router(shadow_router)
    app.include_router(confirm_router)
    app.include_router(devices_router)
    app.include_router(revert_router)
    app.include_router(mobile_router)
    app.include_router(ha_prefs_router)

    return app
