# A-10 · `tools/web_search.py` — Brave Search API

## Overview

Implement `chloe/tools/web_search.py` with verbs `search(query)`, `fetch_page(url)`, and `summarize_url(url)`. Auth: `free` for all three. Include a PII sanitizer that blocks queries containing names from the `persons` table. Return results as `list[{title, url, snippet}]`.

## Context

Web search lets Chloe satisfy her interest-driven curiosity (reading about whales, following a news topic) without any human account access. The PII sanitizer is a hard safety layer: Chloe must not search for Teo's coworkers, exes, or contacts by name. This is both a privacy measure and a trust-building constraint.

## Implementation

```python
# chloe/tools/web_search.py

from chloe.tools.base import Tool, ToolVerb, ToolResult
from chloe.observability.logging import get_logger
import httpx
import json
from pathlib import Path

log = get_logger("tool.web_search")

class WebSearchTool(Tool):
    name = "web_search"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key
        self._base_url = "https://api.search.brave.com/res/v1/web/search"
        self.verbs = {
            "search": ToolVerb(
                name="search",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                    },
                    "required": ["query"],
                },
                auth_class="free",
                reversibility=1.0,
                cost_per_call_usd=0.001,
                description_for_model=(
                    "Search the web. Use for your own curiosity or to research a topic. "
                    "Do NOT include names of people from your contact list."
                ),
                description_for_human="Web search",
            ),
            "fetch_page": ToolVerb(
                name="fetch_page",
                schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "URL to fetch"},
                    },
                    "required": ["url"],
                },
                auth_class="free",
                reversibility=1.0,
                cost_per_call_usd=0.0,
                description_for_model="Fetch and read the content of a web page.",
                description_for_human="Fetch web page",
            ),
            "summarize_url": ToolVerb(
                name="summarize_url",
                schema={
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                    },
                    "required": ["url"],
                },
                auth_class="free",
                reversibility=1.0,
                cost_per_call_usd=0.002,
                description_for_model="Fetch a URL and summarize its content with Flash.",
                description_for_human="Summarize URL",
            ),
        }

    async def execute(self, verb: str, args: dict) -> ToolResult:
        if verb == "search":
            return await self._search(args.get("query", ""))
        elif verb == "fetch_page":
            return await self._fetch_page(args.get("url", ""))
        elif verb == "summarize_url":
            return await self._summarize_url(args.get("url", ""))
        return ToolResult(success=False, error=f"Unknown verb: {verb}")

    async def _search(self, query: str) -> ToolResult:
        if not query.strip():
            return ToolResult(success=False, error="query is required")

        # PII check happens at gate level (B-09), but double-check here
        blocked = sanitize(query, _load_persons())
        if not blocked:
            raise PermissionError(f"Query blocked by PII filter: {query!r}")

        if not self._api_key:
            return ToolResult(success=False, error="BRAVE_API_KEY not configured")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    self._base_url,
                    params={"q": query, "count": 5},
                    headers={"Accept": "application/json", "X-Subscription-Token": self._api_key},
                )
                resp.raise_for_status()
                data = resp.json()

            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("description", ""),
                }
                for r in data.get("web", {}).get("results", [])
            ]
            return ToolResult(success=True, data={"results": results})
        except httpx.HTTPStatusError as e:
            log.error("brave_search_error", status=e.response.status_code)
            return ToolResult(success=False, error=str(e))

    async def _fetch_page(self, url: str) -> ToolResult:
        if not url.startswith(("http://", "https://")):
            return ToolResult(success=False, error="Invalid URL")
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"User-Agent": "ChloeBot/2.0"})
                resp.raise_for_status()
                text = resp.text[:8000]   # cap at 8KB
            return ToolResult(success=True, data={"text": text, "url": url})
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def _summarize_url(self, url: str) -> ToolResult:
        page_result = await self._fetch_page(url)
        if not page_result.success:
            return page_result
        
        text = page_result.data.get("text", "")[:4000]
        
        from chloe.llm.gemini import GeminiClient
        from chloe.config import get_settings
        from chloe.llm.schemas import ClusterSynthesis
        
        settings = get_settings()
        client = GeminiClient(api_key=settings.gemini_api_key.get_secret_value())
        result = await client.flash("synthesize_cluster.md", {"text": text, "url": url}, ClusterSynthesis)
        
        if result:
            return ToolResult(success=True, data={"summary": result["summary"], "url": url})
        return ToolResult(success=False, error="Summarization failed")
```

## PII sanitizer

```python
# In web_search.py

def sanitize(query: str, persons: list[dict]) -> bool:
    """
    Returns True if the query is safe (no PII detected).
    Returns False if the query contains a person's name, alias, or work domain.
    """
    query_lower = query.lower()
    tokens = set(query_lower.split())
    
    for person in persons:
        # Check full name
        name_parts = person.get("name", "").lower().split()
        if all(part in tokens or part in query_lower for part in name_parts if len(part) > 2):
            return False
        
        # Check aliases
        for alias in person.get("aliases", []):
            if alias.lower() in query_lower:
                return False
        
        # Check work domains
        for domain in person.get("work_domains", []):
            if domain.lower() in query_lower:
                return False
    
    return True

def _load_persons() -> list[dict]:
    """Load persons table for PII checking."""
    conn = _get_connection()
    rows = conn.execute("SELECT name, aliases, work_domains FROM persons").fetchall()
    import json as _json
    return [
        {
            "name": r["name"],
            "aliases": _json.loads(r["aliases"] or "[]"),
            "work_domains": _json.loads(r["work_domains"] or "[]"),
        }
        for r in rows
    ]
```

## Dependencies

- A-06 (Tool base classes).
- F-02 (config for `brave_api_key`).
- F-04 (`persons` table for PII filter).

## Testing

### Unit tests — `tests/unit/test_web_search.py`

```python
import pytest
import json
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from chloe.tools.web_search import WebSearchTool, sanitize

# VCR cassette test (using pytest-recording or similar)
@pytest.mark.asyncio
async def test_search_returns_typed_result(respx_mock):
    import respx, httpx
    
    tool = WebSearchTool(api_key="test-key")
    fake_response = {
        "web": {
            "results": [
                {"title": "Whale article", "url": "https://example.com", "description": "About whales"}
            ]
        }
    }
    respx_mock.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(200, json=fake_response)
    )
    
    with patch("chloe.tools.web_search._load_persons", return_value=[]):
        result = await tool.execute("search", {"query": "whale population"})
    
    assert result.success
    assert len(result.data["results"]) == 1
    assert result.data["results"][0]["title"] == "Whale article"

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
```

## Acceptance criteria

- VCR cassette test: canned Brave response → typed `list[{title, url, snippet}]` result.
- Unit test: query containing a name from `persons` raises `PermissionError` before hitting the API.
- `fetch_page` caps response at 8KB.
- `summarize_url` makes a Flash call and returns a summary.
