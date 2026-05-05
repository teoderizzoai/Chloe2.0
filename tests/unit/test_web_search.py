import pytest
import httpx
from unittest.mock import patch, AsyncMock, MagicMock

from chloe.tools.web_search import WebSearchTool, sanitize


def _mock_httpx_client(json_body=None, text_body=None, status_code=200):
    """Build a context-manager mock for httpx.AsyncClient."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    if json_body is not None:
        mock_response.json = MagicMock(return_value=json_body)
    if text_body is not None:
        mock_response.text = text_body

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


@pytest.mark.asyncio
async def test_search_returns_typed_result():
    tool = WebSearchTool(api_key="test-key")
    fake_response = {
        "web": {
            "results": [
                {"title": "Whale article", "url": "https://example.com", "description": "About whales"}
            ]
        }
    }
    mock_client = _mock_httpx_client(json_body=fake_response)

    with patch("chloe.tools.web_search._load_persons", return_value=[]):
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tool.execute("search", {"query": "whale population"})

    assert result.success
    assert len(result.data["results"]) == 1
    assert result.data["results"][0]["title"] == "Whale article"
    assert result.data["results"][0]["url"] == "https://example.com"
    assert result.data["results"][0]["snippet"] == "About whales"


def test_sanitize_allows_clean_query():
    persons = [{"name": "Mark Smith", "aliases": [], "work_domains": []}]
    assert sanitize("whale population 2026", persons) is True


def test_sanitize_blocks_person_name():
    persons = [{"name": "Mark Smith", "aliases": [], "work_domains": []}]
    assert sanitize("mark smith linkedin", persons) is False


def test_sanitize_blocks_alias():
    persons = [{"name": "Someone", "aliases": ["marky"], "work_domains": []}]
    assert sanitize("marky instagram photos", persons) is False


def test_sanitize_blocks_work_domain():
    persons = [{"name": "Someone", "aliases": [], "work_domains": ["acme.com"]}]
    assert sanitize("acme.com employees", persons) is False


@pytest.mark.asyncio
async def test_pii_query_raises_permission_error():
    tool = WebSearchTool(api_key="test-key")
    fake_persons = [{"name": "Alice", "aliases": [], "work_domains": []}]

    with patch("chloe.tools.web_search._load_persons", return_value=fake_persons):
        with pytest.raises(PermissionError):
            await tool.execute("search", {"query": "alice job history"})


@pytest.mark.asyncio
async def test_dry_run_no_api_call():
    tool = WebSearchTool(api_key="test-key")
    preview = tool.dry_run("search", {"query": "ocean"})
    assert "Would" in preview


@pytest.mark.asyncio
async def test_fetch_page_caps_at_8kb():
    tool = WebSearchTool()
    big_text = "x" * 10000
    mock_client = _mock_httpx_client(text_body=big_text)

    with patch("httpx.AsyncClient", return_value=mock_client):
        result = await tool.execute("fetch_page", {"url": "https://example.com/page"})

    assert result.success
    assert len(result.data["text"]) == 8000


@pytest.mark.asyncio
async def test_fetch_page_rejects_invalid_url():
    tool = WebSearchTool()
    result = await tool.execute("fetch_page", {"url": "ftp://example.com"})
    assert not result.success
    assert "Invalid URL" in result.error


@pytest.mark.asyncio
async def test_search_empty_query_returns_error():
    tool = WebSearchTool(api_key="test-key")
    with patch("chloe.tools.web_search._load_persons", return_value=[]):
        result = await tool.execute("search", {"query": "   "})
    assert not result.success
    assert "query is required" in result.error


@pytest.mark.asyncio
async def test_search_no_api_key_returns_error():
    tool = WebSearchTool(api_key=None)
    with patch("chloe.tools.web_search._load_persons", return_value=[]):
        with patch.dict("os.environ", {}, clear=True):
            tool._api_key = None
            result = await tool.execute("search", {"query": "safe query"})
    assert not result.success
    assert "BRAVE_API_KEY" in result.error


@pytest.mark.asyncio
async def test_unknown_verb_returns_error():
    tool = WebSearchTool(api_key="test-key")
    result = await tool.execute("nonexistent", {})
    assert not result.success
    assert "Unknown verb" in result.error
