# F-11 · `app.py` — FastAPI factory and `loop.py` — asyncio bootstrap

## Overview

Implement `chloe/app.py` which creates the FastAPI application, mounts routers for `/metrics`, `/admin`, and `/v1`. Implement `chloe/loop.py` which starts the asyncio event loop, runs `migrate()` at boot, starts background tasks as stubs (they `await asyncio.sleep(∞)`), and launches uvicorn. Running `python -m chloe` must start, serve `GET /` → `200`, and shut down cleanly on `SIGTERM`.

## Context

The current 1.0 entry point is `server.py` which creates a FastAPI app with a `lifespan` context manager that starts the `Chloe` heartbeat. In 2.0, the entry point is split: `app.py` handles the HTTP layer (pure FastAPI, testable without running the loop), and `loop.py` handles all the async orchestration (migrations, background tasks, uvicorn). This separation allows the test client to import `app` without triggering migrations or starting background tasks.

## `app.py`

```python
# chloe/app.py

from fastapi import FastAPI
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Lifespan is intentionally minimal here.
    # Heavy startup (migrations, background tasks) is done in loop.py.
    yield

def create_app() -> FastAPI:
    app = FastAPI(
        title="Chloe",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Health check
    @app.get("/")
    async def health():
        return {"status": "ok", "version": "2.0.0"}

    # Mount metrics
    from chloe.observability.metrics import metrics_router
    app.include_router(metrics_router)

    # Mount admin (stub for now — fleshed out in A-13)
    from chloe.admin.api import admin_router
    app.include_router(admin_router, prefix="/admin")

    # Mount v1 API (stub — fleshed out in later phases)
    from chloe.channels.chat_api import chat_router
    app.include_router(chat_router, prefix="/v1")

    return app

app = create_app()
```

## `loop.py`

```python
# chloe/loop.py

import asyncio
import signal
import uvicorn
from chloe.state.db import migrate
from chloe.observability.logging import configure_logging, get_logger

log = get_logger("loop")

async def _noop_background_task(name: str):
    """Placeholder for background tasks not yet implemented."""
    log.info("background_task_started", task=name)
    await asyncio.sleep(float("inf"))

async def _startup():
    """Run all startup tasks before serving requests."""
    from chloe.config import get_settings
    s = get_settings()
    configure_logging(s.chloe_log_level)
    log.info("chloe_starting", version="2.0.0")

    # Run DB migrations
    n = migrate()
    log.info("migrations_applied", count=n)

    # Start Gemini cache refresh (F-07)
    from chloe.llm.gemini import start_cache_refresh_task
    asyncio.create_task(start_cache_refresh_task(), name="gemini_cache_refresh")

    # Stub background tasks (implementations added per phase)
    for task_name in [
        "initiative_tick",
        "reflect_tick",
        "vitals_tick",
        "consolidate_sleep",
        "weekly_self_model",
        "pending_confirmations_watcher",
    ]:
        asyncio.create_task(
            _noop_background_task(task_name),
            name=task_name,
        )

async def main():
    await _startup()

    from chloe.app import app
    config = uvicorn.Config(
        app=app,
        host="0.0.0.0",
        port=8000,
        log_config=None,   # We use structlog; disable uvicorn's logging
    )
    server = uvicorn.Server(config)

    # Handle SIGTERM gracefully
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, server.handle_exit, signal.SIGTERM, None)

    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())
```

## `__main__.py`

```python
# chloe/__main__.py
from chloe.loop import main
import asyncio
asyncio.run(main())
```

This enables `python -m chloe`.

## Admin router stub

Create a minimal `chloe/admin/api.py`:
```python
from fastapi import APIRouter
admin_router = APIRouter()

@admin_router.get("/")
async def admin_index():
    return {"message": "Chloe admin"}
```

## Chat router stub

Create a minimal `chloe/channels/chat_api.py`:
```python
from fastapi import APIRouter
chat_router = APIRouter()

@chat_router.get("/status")
async def status():
    return {"chat": "ok"}
```

## Dependencies

- F-01 (package structure).
- F-02 (config).
- F-03 (migrate).
- F-09 (logging).
- F-10 (metrics router).

## Testing

### Unit tests — `tests/unit/test_app.py`

```python
import pytest
from fastapi.testclient import TestClient
from chloe.app import create_app

@pytest.fixture
def client():
    return TestClient(create_app())

def test_health_endpoint(client):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

def test_metrics_endpoint_mounted(client):
    response = client.get("/metrics")
    assert response.status_code == 200

def test_admin_endpoint_mounted(client):
    response = client.get("/admin/")
    assert response.status_code == 200

def test_v1_status_endpoint_mounted(client):
    response = client.get("/v1/status")
    assert response.status_code == 200
```

### Smoke test (manual / CI)

```bash
# In CI, run with a timeout
timeout 10 python -m chloe &
PID=$!
sleep 3
curl -sf http://localhost:8000/ | jq .status
kill $PID
```

The CI smoke test should pass within 5 seconds.

### SIGTERM test

```python
@pytest.mark.asyncio
async def test_startup_runs_migrate(tmp_path, monkeypatch):
    from chloe.state import db as db_mod
    calls = []
    original = db_mod.migrate
    def mock_migrate(*a, **kw):
        calls.append(1)
        return original(*a, **kw)
    monkeypatch.setattr(db_mod, "migrate", mock_migrate)
    
    from chloe.loop import _startup
    await _startup()
    assert len(calls) >= 1
```

## Acceptance criteria

- `python -m chloe` starts, serves `GET /` → `200`.
- `GET /metrics` returns `200` with Prometheus content.
- `SIGTERM` shuts down cleanly (no traceback, exit code 0).
- `TestClient(create_app())` works without triggering migrations or background tasks.
