from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from chloe.admin.api import admin_router
from chloe.admin.shadow_routes import router as shadow_router
from chloe.channels.confirm_routes import devices_router, router as confirm_router
from chloe.channels.ha_prefs_routes import router as ha_prefs_router
from chloe.channels.mobile_routes import router as mobile_router
from chloe.channels.dashboard_ws import router as dashboard_ws_router
from chloe.channels.revert_routes import router as revert_router
from chloe.observability.logging import get_logger
from chloe.observability.metrics import metrics_router

log = get_logger("app")


def _sync_memories_to_chroma() -> None:
    """Re-index any SQLite memories missing from ChromaDB."""
    try:
        from chloe.state.db import get_connection
        from chloe.state.chroma import get_collection
        from chloe.memory.retrieval import add_to_chroma

        conn = get_connection()
        rows = conn.execute(
            "SELECT id, kind, text, source FROM memories WHERE archived_tier = 'hot'"
        ).fetchall()
        if not rows:
            return

        collection = get_collection("memories_v2")
        existing_ids = set(collection.get(ids=[str(r["id"]) for r in rows])["ids"])

        synced = 0
        for row in rows:
            if str(row["id"]) not in existing_ids:
                add_to_chroma(
                    memory_id=row["id"],
                    text=row["text"],
                    kind=row["kind"],
                    source=row["source"],
                    artifact_refs=[],
                )
                synced += 1

        log.info("chroma_sync_complete", total=len(rows), synced=synced)
    except Exception as exc:
        log.warning("chroma_sync_failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    from chloe.state.db import migrate
    from chloe.loop import (
        register_all_tools, initiative_loop, daily_job_loop, weekly_job_loop,
        reflect_loop, pressure_loop,
    )

    log.info("startup_begin")

    applied = migrate()
    log.info("migrations_applied", count=applied)

    from chloe.state.db import seed_primary_persons
    seed_primary_persons()
    log.info("primary_persons_seeded")

    _sync_memories_to_chroma()

    register_all_tools()

    from chloe.tools.registry import get_registry
    get_registry().load_dynamic_verbs()

    tasks = [
        asyncio.create_task(initiative_loop(), name="initiative"),
        asyncio.create_task(reflect_loop(),    name="reflect"),
        asyncio.create_task(pressure_loop(),   name="pressure"),
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
    app.include_router(dashboard_ws_router)
    app.include_router(ha_prefs_router)

    _ui_dir = Path(__file__).resolve().parents[1] / "static" / "ui"
    if _ui_dir.exists():
        app.mount("/ui", StaticFiles(directory=_ui_dir, html=True), name="ui")

    return app
