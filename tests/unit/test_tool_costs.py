import pytest

from chloe.tools.costs import TOOL_COSTS, _DEFAULT_COST, get_cost_estimate


def test_known_tool_verb_returns_estimate():
    cost = get_cost_estimate("gmail", "send_reply")
    assert cost.usd == 0.008
    assert "gemini_flash" in cost.breakdown


def test_unknown_tool_returns_default():
    cost = get_cost_estimate("nonexistent_tool", "some_verb")
    assert cost.usd == _DEFAULT_COST.usd


def test_unknown_verb_returns_default():
    cost = get_cost_estimate("gmail", "nonexistent_verb")
    assert cost.usd == _DEFAULT_COST.usd


def test_free_tools_have_zero_cost():
    for verb_cost in TOOL_COSTS.get("spotify", {}).values():
        assert verb_cost.usd == 0.0


def test_smart_home_zero_cost():
    for verb_cost in TOOL_COSTS.get("smart_home", {}).values():
        assert verb_cost.usd == 0.0


def test_all_tools_have_non_negative_costs():
    for tool, verbs in TOOL_COSTS.items():
        for verb_name, cost in verbs.items():
            assert cost.usd >= 0.0, f"{tool}.{verb_name} has negative cost"


def test_gmail_send_reply_below_deliberation_threshold():
    cost = get_cost_estimate("gmail", "send_reply")
    assert cost.usd < 0.10


def test_maps_directions_cost():
    cost = get_cost_estimate("maps", "directions")
    assert cost.usd == 0.01
    assert "google_maps" in cost.breakdown


def test_web_search_has_cost():
    cost = get_cost_estimate("web_search", "search")
    assert cost.usd > 0.0


def test_realize_attaches_cost_estimate():
    from chloe.initiative.candidates import CandidateAction
    from chloe.initiative.engine import realize

    candidate = CandidateAction(
        tool="gmail",
        verb="draft_reply",
        args={},
        intent="Reply to email",
        source="test",
        source_id="test_id",
        pressure=0.5,
    )
    action = realize(candidate)
    assert action.cost_estimate is not None
    assert action.cost_estimate.usd == 0.008
