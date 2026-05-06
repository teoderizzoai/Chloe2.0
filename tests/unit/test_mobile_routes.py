"""Tests for mobile API routes (F-M02, F-M05, F-M06, F-M07, F-M08)."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


# ── F-M05 · Activity tab ─────────────────────────────────────────────────────

class TestMobileAudit:
    def test_returns_200(self, client):
        resp = client.get("/v1/audit")
        assert resp.status_code == 200

    def test_json_schema(self, client):
        data = client.get("/v1/audit").json()
        assert "count" in data
        assert "actions" in data
        assert "offset" in data

    def test_empty_db_returns_zero_actions(self, client):
        data = client.get("/v1/audit").json()
        assert data["count"] == 0
        assert data["actions"] == []

    def test_limit_param(self, client):
        resp = client.get("/v1/audit?limit=5")
        assert resp.status_code == 200

    def test_offset_param(self, client):
        resp = client.get("/v1/audit?offset=10")
        assert resp.status_code == 200


# ── F-M06 · "Now" tab ────────────────────────────────────────────────────────

class TestStateNow:
    def test_returns_200(self, client):
        resp = client.get("/v1/state/now")
        assert resp.status_code == 200

    def test_schema(self, client):
        data = client.get("/v1/state/now").json()
        assert "current_activity" in data
        assert "affect_label" in data
        assert "tone" in data
        assert "goals" in data
        assert "top_interests" in data

    def test_goals_is_list(self, client):
        data = client.get("/v1/state/now").json()
        assert isinstance(data["goals"], list)

    def test_top_interests_is_list(self, client):
        data = client.get("/v1/state/now").json()
        assert isinstance(data["top_interests"], list)


# ── F-M07 · Leash settings ───────────────────────────────────────────────────

class TestPreferenceUpdate:
    def test_update_string_pref(self, client):
        resp = client.patch(
            "/v1/preferences",
            json={"key": "auth_ceiling", "value": "intimate"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["key"] == "auth_ceiling"

    def test_update_bool_pref(self, client):
        resp = client.patch(
            "/v1/preferences",
            json={"key": "away_mode", "value": True},
        )
        assert resp.status_code == 200

    def test_update_dict_pref(self, client):
        resp = client.patch(
            "/v1/preferences",
            json={"key": "quiet_hours", "value": {"start": "22:00", "end": "08:00"}},
        )
        assert resp.status_code == 200

    def test_preference_persists(self, client):
        from chloe.state.db import get_connection
        import json

        client.patch("/v1/preferences", json={"key": "test_key", "value": "test_val"})
        row = get_connection().execute(
            "SELECT value FROM preferences WHERE key='test_key'"
        ).fetchone()
        assert row is not None
        assert json.loads(row["value"]) == "test_val"


# ── F-M08 · OAuth revoke ─────────────────────────────────────────────────────

class TestOAuthRevoke:
    def test_revoke_returns_200(self, client, monkeypatch):
        from chloe.state import oauth_tokens

        stored = {}

        def _fake_store(service, data):
            stored[service] = data

        monkeypatch.setattr(oauth_tokens, "store", _fake_store)

        resp = client.delete("/v1/oauth/spotify")
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"
        assert resp.json()["service"] == "spotify"
        assert stored.get("spotify") == {}

    def test_revoke_google(self, client, monkeypatch):
        from chloe.state import oauth_tokens
        monkeypatch.setattr(oauth_tokens, "store", lambda s, d: None)
        resp = client.delete("/v1/oauth/google")
        assert resp.status_code == 200


# ── Mobile routes registered in app ─────────────────────────────────────────

class TestRoutesRegistered:
    def test_voice_ws_route_exists(self, client):
        routes = [r.path for r in client.app.routes]
        assert "/v1/voice" in routes

    def test_mobile_ws_route_exists(self, client):
        routes = [r.path for r in client.app.routes]
        assert "/v1/mobile/ws" in routes

    def test_audit_route_exists(self, client):
        routes = [r.path for r in client.app.routes]
        assert "/v1/audit" in routes
