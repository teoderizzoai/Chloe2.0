import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from chloe.initiative.shadow import shadow_tick


@pytest.mark.asyncio
async def test_shadow_tick_logs_without_executing(monkeypatch):
    saved = {}

    def mock_kv_set(key, val):
        saved[key] = val

    def mock_kv_get(key, **kw):
        return kw.get("default")

    monkeypatch.setattr("chloe.initiative.shadow.kv_get", mock_kv_get)
    monkeypatch.setattr("chloe.initiative.shadow.kv_set", mock_kv_set)

    async def tracking_tick():
        from chloe.initiative.engine import gate_submit
        from chloe.actions.schema import Action
        action = Action(tool="messages", verb="send_text", args={},
                        intent="Morning check-in", preview="Morning check-in",
                        authorization="kinetic")
        await gate_submit(action)

    monkeypatch.setattr("chloe.initiative.shadow.new_tick", tracking_tick)

    await shadow_tick()

    assert "shadow_decisions" in saved
    records = saved["shadow_decisions"]
    assert len(records) == 1
    assert records[0]["proposed"]["tool"] == "messages"


@pytest.mark.asyncio
async def test_shadow_tick_idle_logs_correctly(monkeypatch):
    saved = {}
    monkeypatch.setattr("chloe.initiative.shadow.kv_get", lambda k, **kw: kw.get("default"))
    monkeypatch.setattr("chloe.initiative.shadow.kv_set", lambda k, v: saved.update({k: v}))

    async def idle_tick():
        pass

    monkeypatch.setattr("chloe.initiative.shadow.new_tick", idle_tick)

    await shadow_tick()
    records = saved.get("shadow_decisions", [])
    assert records[0]["was_idle"] is True


@pytest.mark.asyncio
async def test_shadow_endpoint_returns_summary(monkeypatch):
    from httpx import AsyncClient
    from chloe.app import create_app
    from chloe.state.kv import set as kv_set

    kv_set("shadow_decisions", [
        {"timestamp": "2026-05-04T10:00:00", "proposed": {"tool": "messages", "verb": "send_text", "intent": "check in", "authorization": "kinetic"}, "was_idle": False},
        {"timestamp": "2026-05-04T10:01:00", "proposed": None, "was_idle": True},
    ])

    from pathlib import Path
    from chloe.state.db import migrate, close
    MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"

    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        migrate(db_path=os.path.join(tmp, "test.db"), migrations_dir=MIGRATIONS_DIR)

        app = create_app()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/admin/shadow")

        close()

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_ticks"] == 2
    assert data["summary"]["new_engine_idle"] == 1
    assert "messages" in data["summary"]["by_tool"]
