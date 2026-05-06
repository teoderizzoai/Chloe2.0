from fastapi import FastAPI
from chloe.admin.api import admin_router
from chloe.admin.shadow_routes import router as shadow_router
from chloe.channels.confirm_routes import devices_router, router as confirm_router
from chloe.channels.mobile_routes import router as mobile_router
from chloe.channels.revert_routes import router as revert_router


def create_app() -> FastAPI:
    app = FastAPI(title="Chloe 2.0")
    app.include_router(admin_router, prefix="/admin")
    app.include_router(shadow_router)
    app.include_router(confirm_router)
    app.include_router(devices_router)
    app.include_router(revert_router)
    app.include_router(mobile_router)
    return app
