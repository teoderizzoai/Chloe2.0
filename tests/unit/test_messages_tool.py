import pytest
from chloe.tools.messages import MessagesTool


@pytest.fixture
def tool_with_mock():
    sent = []

    async def mock_send(body):
        sent.append(body)
        return True

    tool = MessagesTool(send_callback=mock_send)
    return tool, sent


@pytest.mark.asyncio
async def test_dry_run_no_api_call():
    tool = MessagesTool(send_callback=None)
    preview = tool.dry_run("send_text", {"body": "hello world"})
    assert "Would send" in preview
    assert "hello world" in preview


@pytest.mark.asyncio
async def test_dry_run_does_not_call_callback():
    called = []

    async def mock_send(body):
        called.append(body)
        return True

    tool = MessagesTool(send_callback=mock_send)
    tool.dry_run("send_text", {"body": "test"})
    assert len(called) == 0


@pytest.mark.asyncio
async def test_send_text_calls_callback(tool_with_mock):
    tool, sent = tool_with_mock
    result = await tool.execute("send_text", {"body": "hello"})
    assert result.success
    assert len(sent) == 1
    assert sent[0] == "hello"


@pytest.mark.asyncio
async def test_send_text_empty_body_fails(tool_with_mock):
    tool, sent = tool_with_mock
    result = await tool.execute("send_text", {"body": ""})
    assert not result.success
    assert "body is required" in result.error


@pytest.mark.asyncio
async def test_send_text_no_callback_fails():
    tool = MessagesTool(send_callback=None)
    result = await tool.execute("send_text", {"body": "hello"})
    assert not result.success


@pytest.mark.asyncio
async def test_send_voice_returns_not_implemented(tool_with_mock):
    tool, _ = tool_with_mock
    result = await tool.execute("send_voice", {"audio_file": "test.mp3"})
    assert not result.success
    assert "Phase F" in result.error


def test_verb_auth_is_kinetic():
    tool = MessagesTool()
    assert tool.verbs["send_text"].auth_class == "kinetic"
    assert tool.verbs["send_voice"].auth_class == "kinetic"
