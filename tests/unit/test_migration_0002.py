import pytest
import sqlite3
from pathlib import Path
from chloe.state.db import migrate, get_connection, close

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def reset():
    yield
    close()


def test_all_tables_created(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "actions" in tables
    assert "artifact_index" in tables
    assert "preferences" in tables
    assert "budgets" in tables


def test_budgets_seeded(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    windows = {r[0] for r in conn.execute("SELECT window FROM budgets").fetchall()}
    assert windows == {"today", "this_hour", "this_week"}


def test_preferences_seeded(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    keys = {r[0] for r in conn.execute("SELECT key FROM preferences").fetchall()}
    assert "quiet_hours" in keys
    assert "auth_ceiling" in keys
    assert "spending_cap_usd_day" in keys


def test_actions_state_constraint(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    conn.execute("INSERT INTO persons (name) VALUES ('Teo')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO actions (id, tool, verb, intent, authorization, preview, state)
            VALUES ('test-id', 'spotify', 'queue_track', 'test', 'kinetic', 'preview', 'invalid_state')
        """)
        conn.commit()


def test_actions_authorization_constraint(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    conn = get_connection(tmp_path / "test.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("""
            INSERT INTO actions (id, tool, verb, intent, authorization, preview)
            VALUES ('test-id2', 'spotify', 'queue_track', 'test', 'superkinetic', 'preview')
        """)
        conn.commit()


def test_chloe_actions_counter_can_increment():
    from chloe.observability.metrics import record_action
    record_action("spotify", "queue_track", "executed")
