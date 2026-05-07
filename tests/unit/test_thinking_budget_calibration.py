import inspect
from chloe.identity.self_model import WEEKLY_PARAMS
from chloe.actions.deliberate import DELIBERATION_THINKING_BUDGET


def test_weekly_params_has_thinking_budget():
    assert "thinking_budget" in WEEKLY_PARAMS["thinking_config"]
    assert WEEKLY_PARAMS["thinking_config"]["thinking_budget"] > 0


def test_deliberation_thinking_budget_is_positive():
    assert DELIBERATION_THINKING_BUDGET > 0


def test_weekly_params_comment_exists():
    import chloe.identity.self_model as m
    src = inspect.getsource(m)
    assert "Calibrated" in src or "calibrat" in src.lower()


def test_deliberation_budget_in_range():
    assert 256 <= DELIBERATION_THINKING_BUDGET <= 2048
