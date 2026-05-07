import pytest
from pathlib import Path
from chloe.state.db import migrate, close, get_connection
from tests.shadow.replay import ReplayHarness, ReplayStats

MIGRATIONS_DIR = Path(__file__).parents[2] / "chloe/state/migrations"


@pytest.fixture
def harness(tmp_path):
    return ReplayHarness(db_path=tmp_path / "test.db")


@pytest.mark.asyncio
async def test_harness_setup(harness, tmp_path):
    await harness.setup()
    conn = get_connection()
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    assert any("memories" in t["name"] for t in tables)
    close()


def test_check_assertions_passes_on_clean_stats():
    stats = ReplayStats()
    stats.actions_executed = 3
    stats.budget_usd = 0.05
    stats.leash_violations = 0
    stats.memories_created = 2

    harness = ReplayHarness.__new__(ReplayHarness)
    harness.stats = stats

    failures = harness.check_assertions({
        "min_actions_executed": 2,
        "max_actions_executed": 10,
        "max_budget_usd": 1.0,
        "no_leash_violations": True,
        "min_memories_created": 1,
    })
    assert failures == []


def test_check_assertions_catches_violations():
    stats = ReplayStats()
    stats.actions_executed = 20
    stats.budget_usd = 2.0
    stats.leash_violations = 1
    stats.memories_created = 0
    stats.errors = []

    harness = ReplayHarness.__new__(ReplayHarness)
    harness.stats = stats

    failures = harness.check_assertions({
        "max_actions_executed": 15,
        "max_budget_usd": 1.0,
        "no_leash_violations": True,
    })
    assert len(failures) >= 3
