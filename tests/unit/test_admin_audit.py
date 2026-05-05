import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chloe.actions import audit
from chloe.actions.schema import Action
from chloe.app import create_app
from chloe.state.db import close, migrate

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


@pytest.fixture
def client():
    return TestClient(create_app())


def test_audit_endpoint_returns_200(client):
    response = client.get("/admin/audit")
    assert response.status_code == 200


def test_audit_endpoint_json_schema(client):
    response = client.get("/admin/audit")
    data = response.json()
    assert "count" in data
    assert "actions" in data
    assert isinstance(data["actions"], list)


def test_audit_endpoint_shows_actions(client):
    a = Action(
        tool="spotify", verb="queue_track",
        intent="play calming music",
        preview="Queue Bloom",
        authorization="kinetic",
        state="executed",
    )
    asyncio.run(audit.append(a))

    response = client.get("/admin/audit")
    data = response.json()
    assert data["count"] >= 1
    assert any(item["tool"] == "spotify" for item in data["actions"])


def test_audit_endpoint_correct_schema_fields(client):
    a = Action(
        tool="notes", verb="create",
        intent="write a note",
        preview="Create note",
        authorization="kinetic",
    )
    asyncio.run(audit.append(a))

    response = client.get("/admin/audit")
    item = response.json()["actions"][0]
    required_fields = {"id", "tool", "verb", "intent", "state", "authorization", "proposed_at"}
    assert required_fields.issubset(item.keys())


def test_audit_ui_returns_html(client):
    response = client.get("/admin/audit/ui")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Audit Feed" in response.text


def test_audit_limit_param(client):
    response = client.get("/admin/audit?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["actions"]) <= 5
