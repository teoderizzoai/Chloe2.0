import pytest
from fastapi.testclient import TestClient

from chloe.app import create_app


@pytest.fixture
def client():
    return TestClient(create_app())


def test_cache_status_no_cache(client, monkeypatch):
    import chloe.llm.gemini as g
    monkeypatch.setattr(g, "_cache_name", None)
    response = client.get("/admin/cache/status")
    assert response.status_code == 200
    data = response.json()
    assert data["active"] is False
    assert data["cache_name"] is None


def test_cache_status_with_cache(client, monkeypatch):
    import chloe.llm.gemini as g
    monkeypatch.setattr(g, "_cache_name", "cachedContents/test-abc")
    response = client.get("/admin/cache/status")
    assert response.status_code == 200
    data = response.json()
    assert data["cache_name"] == "cachedContents/test-abc"
    assert data["active"] is True


def test_cache_status_has_ttl(client, monkeypatch):
    import chloe.llm.gemini as g
    monkeypatch.setattr(g, "_cache_name", None)
    data = client.get("/admin/cache/status").json()
    assert "ttl_seconds" in data
    assert data["ttl_seconds"] == 3600


def test_describe_static_includes_tool_names():
    from chloe.tools.base import Tool, ToolVerb
    from chloe.tools.registry import ToolRegistry

    class DummyTool(Tool):
        name = "dummy_b08_tool"
        def __init__(self):
            self.verbs = {"ping": ToolVerb(
                name="ping", schema={}, auth_class="free", reversibility=1.0,
                description_for_model="Pings", description_for_human="Ping",
            )}
        async def execute(self, verb, args): pass

    r = ToolRegistry()
    r.register(DummyTool())
    desc = r.describe_static()
    assert "dummy_b08_tool" in desc
    assert "ping" in desc
