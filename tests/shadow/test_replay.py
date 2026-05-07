import pytest
from pathlib import Path
from chloe.state.db import close

TAPE_PATH = Path(__file__).parent / "tape_24h.json"


@pytest.mark.asyncio
@pytest.mark.shadow
async def test_24h_tape_replay(tmp_path):
    """Replay the 24-hour tape and assert all invariants."""
    from tests.shadow.replay import run_replay

    success, failures = await run_replay(
        db_path=tmp_path / "replay.db",
        tape_path=TAPE_PATH,
    )

    if failures:
        failure_msg = "\n".join(failures)
        pytest.fail(f"Replay assertions failed:\n{failure_msg}")

    close()


@pytest.mark.asyncio
@pytest.mark.shadow
async def test_replay_no_budget_exceeded(tmp_path):
    """Specific check: budget stays within cap during 24h tape."""
    from tests.shadow.replay import run_replay

    success, failures = await run_replay(
        db_path=tmp_path / "replay_budget.db",
        tape_path=TAPE_PATH,
    )

    budget_failures = [f for f in failures if "Budget exceeded" in f]
    assert not budget_failures, "\n".join(budget_failures)
    close()


@pytest.mark.asyncio
@pytest.mark.shadow
async def test_replay_no_quiet_hours_violations(tmp_path):
    """Specific check: no initiative actions fire during quiet hours."""
    from tests.shadow.replay import run_replay

    success, failures = await run_replay(
        db_path=tmp_path / "replay_quiet.db",
        tape_path=TAPE_PATH,
    )

    violation_failures = [f for f in failures if "Leash violation" in f]
    assert not violation_failures, "\n".join(violation_failures)
    close()
