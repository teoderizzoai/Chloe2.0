import ast
import pathlib
import pytest


def test_discord_send_only_called_from_messages_tool():
    """No file other than tools/messages.py may call discord send functions directly."""
    forbidden_callers = []
    chloe_dir = pathlib.Path("chloe")

    for py_file in chloe_dir.rglob("*.py"):
        if py_file.name == "messages.py" and "tools" in py_file.parts:
            continue
        if py_file.name == "discord_bot.py":
            continue  # source of truth, may define send_dm

        source = py_file.read_text()
        if "send_dm" not in source and "channel.send" not in source:
            continue

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = ast.dump(node.func)
                if "send_dm" in func or ("send" in func and "channel" in ast.dump(node)):
                    forbidden_callers.append(f"{py_file}:{node.lineno}")

    assert not forbidden_callers, (
        "Direct Discord sends found outside tools/messages.py:\n"
        + "\n".join(forbidden_callers)
    )


def test_no_direct_discord_send_in_chloe_py():
    """_send_autonomous_outreach in chloe.py must use gate.submit, not a direct Discord callback."""
    chloe_py = pathlib.Path("chloe/chloe.py")
    if not chloe_py.exists():
        pytest.skip("chloe.py not found")

    source = chloe_py.read_text()
    assert "gate.submit" in source, "gate.submit not found in chloe.py"
    assert "self.on_message" not in source, "Direct on_message callback found in chloe.py"
