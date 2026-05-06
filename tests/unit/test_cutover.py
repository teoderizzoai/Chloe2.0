"""
D-10 smoke tests: verify the cutover to the new initiative engine is complete.

Note: test_no_shadow_runner requires shadow.py to be deleted from chloe/ after
the shadow observation period ends. During shadow mode both tests are expected to
remain skipped / manual. Once the operator deletes shadow.py and shadow_routes.py,
all tests here should pass.
"""
import subprocess
import pytest


def test_no_fire_event_in_codebase():
    """_fire_event must not exist in the chloe package after cutover."""
    result = subprocess.run(
        ["grep", "-r", "_fire_event", "chloe/"],
        capture_output=True, text=True,
        cwd=subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip(),
    )
    assert result.stdout.strip() == "", f"_fire_event found:\n{result.stdout}"


def test_initiative_loop_uses_new_engine():
    """loop.py must import from initiative.engine."""
    result = subprocess.run(
        ["grep", "-r", "initiative.engine", "chloe/loop.py"],
        capture_output=True, text=True,
        cwd=subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True).stdout.strip(),
    )
    assert result.stdout.strip() != "", "loop.py doesn't import initiative.engine"


def test_initiative_threshold_in_config():
    from chloe.config import get_settings
    settings = get_settings()
    assert hasattr(settings, "initiative_threshold")
    assert 0.2 <= settings.initiative_threshold <= 0.8, "Threshold out of reasonable range"
