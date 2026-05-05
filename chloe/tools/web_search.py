import json
import os

import httpx

from chloe.observability.logging import get_logger
from chloe.state.db import get_connection
from chloe.tools.base import Tool, ToolResult, ToolVerb

log = get_logger("tool.web_search")


def sanitize(query: str, persons: list[dict]) -> bool:
    """Returns True if query is safe (no PII), False if a person name/alias/domain is found."""
    query_lower = query.lower()
    tokens = set(query_lower.split())

    for person in persons:
        name_parts = person.get("name", "").lower().split()
        if all(part in tokens or part in query_lower for part in name_parts if len(part) > 2):
            return False

        for alias in person.get("aliases", []):
            if alias.lower() in query_lower:
                return False

        for domain in person.get("work_domains", []):
            if domain.lower() in query_lower:
                return False

    return True


def _load_persons() -> list[dict]:
    conn = get_connection()
    rows = conn.execute("SELECT name, aliases, work_domains FROM persons").fetchall()
    return [
        {
            "name": r["name"],
            "aliases": json.loads(r["aliases"] or "[]"),
            "work_domains": json.loads(r["work_domains"] or "[]"),
        }
        for r in rows
    ]


class WebSearchTool(Tool):
    name = "web_search"

    def __init__(self, api_key: str | None = None):
        self._api_key = api_key or os.environ.get("BRAVE_API_KEY")
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

        is_safe = sanitize(query, _load_persons())
        if not is_safe:
            raise PermissionError(f"Query blocked by PII filter: {query!r}")

        if not self._api_key:
            return ToolResult(success=False, error="BRAVE_API_KEY not configured")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    self._base_url,
                    params={"q": query, "count": 5},
                    headers={
                        "Accept": "application/json",
                        "X-Subscription-Token": self._api_key,
                    },
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
                text = resp.text[:8000]  # cap at 8KB
            return ToolResult(success=True, data={"text": text, "url": url})
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    async def _summarize_url(self, url: str) -> ToolResult:
        page_result = await self._fetch_page(url)
        if not page_result.success:
            return page_result

        text = page_result.data.get("text", "")[:4000]

        from chloe.llm.gemini import GeminiClient
        from chloe.llm.schemas import ClusterSynthesis

        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        client = GeminiClient(api_key=gemini_key)
        result = await client.flash("synthesize_cluster.md", {"text": text, "url": url}, ClusterSynthesis)

        if result:
            return ToolResult(success=True, data={"summary": result["summary"], "url": url})
        return ToolResult(success=False, error="Summarization failed")
