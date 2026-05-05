import pathlib
import pytest


def test_discord_send_only_in_messages_tool():
    """
    No Python file other than tools/messages.py and discord_bot.py
    should directly call send_dm or channel.send.
    """
    chloe_root = pathlib.Path("chloe")
    violations = []

    for py_file in sorted(chloe_root.rglob("*.py")):
        if "messages" in py_file.name and "tools" in py_file.parts:
            continue
        if py_file.name == "discord_bot.py":
            continue

        try:
            source = py_file.read_text(encoding="utf-8")
        except Exception:
            continue

        direct_patterns = ["send_dm(", "channel.send(", ".send(mention", "on_message(reply"]
        for pattern in direct_patterns:
            if pattern in source:
                violations.append(f"{py_file}: contains '{pattern}'")

    assert not violations, (
        "Direct Discord send calls found outside tools/messages.py:\n"
        + "\n".join(violations)
    )


def test_gate_submit_used_for_outreach():
    """
    The chloe.py file should reference gate.submit for outreach.
    """
    chloe_py = pathlib.Path("chloe/chloe.py")
    if not chloe_py.exists():
        pytest.skip("chloe.py not yet modified")

    source = chloe_py.read_text()
    assert "gate.submit" in source or "gate import" in source, (
        "chloe.py does not use gate.submit for outreach"
    )
