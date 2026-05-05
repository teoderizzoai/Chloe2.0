import pytest

from chloe.tools.reminders import RemindersTool


@pytest.fixture
def tool():
    return RemindersTool()


def test_dry_run_add(tool):
    preview = tool.dry_run("add", {"title": "Call dentist", "due": "2026-05-05"})
    assert "Call dentist" in preview
    assert "2026-05-05" in preview


def test_dry_run_add_no_due(tool):
    preview = tool.dry_run("add", {"title": "Buy milk"})
    assert "Buy milk" in preview


def test_all_verbs_defined(tool):
    assert "add" in tool.verbs
    assert "complete" in tool.verbs
    assert "list" in tool.verbs


def test_add_is_kinetic(tool):
    assert tool.verbs["add"].auth_class == "kinetic"


def test_list_is_intimate(tool):
    assert tool.verbs["list"].auth_class == "intimate"


@pytest.mark.asyncio
async def test_no_token_returns_error(tool, monkeypatch):
    monkeypatch.setattr("chloe.tools.reminders.load_token", lambda s: None)
    result = await tool.execute("add", {"title": "Test"})
    assert not result.success
    assert "token" in result.error.lower() or "OAuth" in result.error
