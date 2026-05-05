import pytest
from pathlib import Path
from chloe.state.db import migrate, get_connection, close
from chloe.actions.schema import Action
from chloe.actions import budget

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    migrate(db_path=tmp_path / "test.db", migrations_dir=MIGRATIONS_DIR)
    yield
    close()


def make_action(auth="kinetic"):
    return Action(
        tool="spotify", verb="queue_track",
        intent="test", preview="test",
        authorization=auth,
    )


def test_charge_updates_today():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.50', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=0 WHERE window='today'")
    conn.commit()

    budget.charge("gemini-2.5-flash", {"input_tokens": 100, "output_tokens": 50})

    row = get_connection().execute(
        "SELECT usd FROM budgets WHERE window='today'"
    ).fetchone()
    assert row["usd"] > 0


def test_not_exceeded_below_cap():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.50', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=1.49 WHERE window='today'")
    conn.commit()
    assert not budget.exceeded_for(make_action())


def test_exceeded_at_cap():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.50', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=1.50 WHERE window='today'")
    conn.commit()
    assert budget.exceeded_for(make_action())


def test_exceeded_after_charge_pushes_over():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.50', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=1.49 WHERE window='today'")
    conn.commit()
    assert not budget.exceeded_for(make_action())
    budget.charge("gemini-2.5-pro", {"input_tokens": 1_000, "output_tokens": 2_000})
    assert budget.exceeded_for(make_action())


def test_throttle_level_proportional():
    conn = get_connection()
    conn.execute("UPDATE preferences SET value=? WHERE key=?",
                 ('1.00', 'spending_cap_usd_day'))
    conn.execute("UPDATE budgets SET usd=0.50 WHERE window='today'")
    conn.commit()
    level = budget.throttle_level()
    assert 0.49 < level < 0.51


def test_throttle_level_zero_when_empty():
    conn = get_connection()
    conn.execute("UPDATE budgets SET usd=0 WHERE window='today'")
    conn.commit()
    assert budget.throttle_level() == pytest.approx(0.0)


def test_reset_windows_zeroes_expired():
    conn = get_connection()
    past = "2020-01-01T00:00:00"
    conn.execute("UPDATE budgets SET usd=1.0, reset_at=? WHERE window='today'", (past,))
    conn.commit()
    budget.reset_windows()
    row = conn.execute("SELECT usd FROM budgets WHERE window='today'").fetchone()
    assert row["usd"] == 0.0
