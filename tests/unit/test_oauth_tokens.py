import pytest
import os
import base64
from pathlib import Path


@pytest.fixture(autouse=True)
def set_master_key(monkeypatch):
    key = os.urandom(32)
    monkeypatch.setenv("CHLOE_MASTER_KEY_INLINE", base64.b64encode(key).decode())


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from chloe.state import db
    from chloe.state import kv as kv_mod
    import chloe.config as config_mod

    # Reset singletons
    db._connection = None
    config_mod._settings = None

    MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"
    db.migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    db.close()
    config_mod._settings = None


def test_store_and_load_roundtrip():
    from chloe.state.oauth_tokens import store, load
    token = {
        "access_token": "tok_abc123",
        "refresh_token": "ref_xyz",
        "expires_in": 3600,
    }
    store("spotify", token)
    loaded = load("spotify")
    assert loaded is not None
    assert loaded["access_token"] == "tok_abc123"
    assert loaded["refresh_token"] == "ref_xyz"


def test_load_returns_none_when_not_stored():
    from chloe.state.oauth_tokens import load
    assert load("nonexistent_service") is None


def test_stored_token_not_plaintext_in_kv():
    from chloe.state import kv
    from chloe.state.oauth_tokens import store
    token = {"access_token": "supersecret_tok_abc"}
    store("test_service", token)
    raw = kv.get("oauth_token:test_service")
    assert "supersecret_tok_abc" not in str(raw)


def test_logs_do_not_contain_token(caplog):
    import logging
    from chloe.state.oauth_tokens import store
    token = {"access_token": "DO_NOT_LOG_ME"}
    with caplog.at_level(logging.INFO):
        store("test_service2", token)
    assert "DO_NOT_LOG_ME" not in caplog.text
