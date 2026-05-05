from fastapi import FastAPI
from chloe.admin.api import admin_router


def create_app() -> FastAPI:
    app = FastAPI(title="Chloe 2.0")
    app.include_router(admin_router, prefix="/admin")
    return app
