"""Web search — Wikipedia API (structured, no API key) + DuckDuckGo fallback."""
from __future__ import annotations

import re
from typing import Any

import httpx

from yomai import tool

_USER_AGENT = "YomaiResearchDemo/1.0 (python-httpx; mailto:research@example.com)"


@tool
def web_search(query: str, limit: int = 5) -> str:
    """Search Wikipedia and return formatted results with citations.

    Best for: facts, people, places, history, science. Falls back to
    DuckDuckGo HTML for broad web coverage.

    Args:
        query: The search query.
        limit: Maximum number of results (default 5).
    """
    result = _wikipedia_search(query, limit)
    if result:
        return result

    result = _ddg_search(query, limit)
    if result:
        return result

    return f"No results found for: {query}"


def _wikipedia_search(query: str, limit: int) -> str:
    """Search Wikipedia API and return formatted results. Returns '' on failure."""
    try:
        client = httpx.Client(timeout=12.0, headers={"User-Agent": _USER_AGENT})
        try:
            # Step 1: search for page titles
            search_resp = client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": limit,
                    "format": "json",
                },
            )
            data = search_resp.json()
            results: list[dict[str, Any]] = data.get("query", {}).get("search", [])
            if not results:
                return ""

            lines: list[str] = []
            for i, r in enumerate(results, 1):
                title = r["title"]
                snippet = r["snippet"].replace(
                    "<span class=\"searchmatch\">", ""
                ).replace("</span>", "")
                page_id = r["pageid"]

                # Step 2: fetch short excerpt from the page
                extract_resp = client.get(
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "prop": "extracts",
                        "exintro": True,
                        "explaintext": True,
                        "pageids": page_id,
                        "exsentences": 3,
                        "format": "json",
                    },
                )
                extract_data = extract_resp.json()
                pages = extract_data.get("query", {}).get("pages", {})
                excerpt = pages.get(str(page_id), {}).get("extract", "")
                if len(excerpt or "") > 300:
                    excerpt = excerpt[:300] + "..."

                lines.append(f"{i}. {title}")
                if excerpt:
                    lines.append(f"   {excerpt}")
                else:
                    lines.append(f"   {snippet}")
                lines.append(f"   https://en.wikipedia.org/?curid={page_id}")

            return "\n\n".join(lines)
        finally:
            client.close()
    except Exception:
        return ""


def _ddg_search(query: str, limit: int) -> str:
    """Search DuckDuckGo HTML. Returns '' on failure."""
    try:
        client = httpx.Client(timeout=12.0, headers={"User-Agent": _USER_AGENT})
        try:
            resp = client.get(
                "https://lite.duckduckgo.com/lite/",
                params={"q": query, "kl": "wt-wt"},
            )
            return _parse_ddg(resp.text, limit)
        finally:
            client.close()
    except Exception:
        return ""


def _parse_ddg(html: str, limit: int) -> str:
    """Parse DuckDuckGo lite HTML into result strings."""
    results: list[str] = []
    seen: set[str] = set()

    # Find result blocks: <a href=URL>title</a> followed by snippet text
    # Pattern: anchor with URL, then bold snippet text before <br>
    blocks = re.split(r"<br\s*/?>", html)
    i = 0
    for block in blocks:
        if len(results) >= limit:
            break
        # Find URL + title
        link_m = re.search(r'<a\s+href="(https?://[^"]+)"[^>]*>([^<]+)</a>', block)
        if not link_m:
            continue
        url = link_m.group(1)
        title = re.sub(r"<[^>]+>", "", link_m.group(2)).strip()
        if not title or url in seen:
            continue
        skip = ("duckduckgo.com/?", "lite.duckduckgo", "help.duckduckgo",
                "add.duckduckgo", "r.duckduckgo")
        if any(s in url for s in skip):
            continue
        seen.add(url)

        # Get snippet: text after the link, stripped of tags
        rest = block[link_m.end():]
        snippet = re.sub(r"<[^>]+>", " ", rest).strip()
        snippet = re.sub(r"\s+", " ", snippet)
        snippet = snippet[:200].strip()
        if not snippet or len(snippet) < 15:
            continue

        results.append(f"{len(results) + 1}. {title}\n   {snippet}\n   {url}")

    return "\n\n".join(results) if results else ""


@tool
def wikipedia_lookup(topic: str) -> str:
    """Get the full summary of a Wikipedia article by exact title.

    Use this when you already know the article title (e.g., "Donald Trump",
    not a question like "Who was born in 1946?").

    Args:
        topic: The Wikipedia article title (e.g., "Quantum computing").
    """
    try:
        client = httpx.Client(timeout=12.0, headers={"User-Agent": _USER_AGENT})
        try:
            resp = client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query",
                    "prop": "extracts",
                    "explaintext": True,
                    "titles": topic,
                    "exsentences": 8,
                    "format": "json",
                },
            )
            data = resp.json()
            pages = data.get("query", {}).get("pages", {})
            for page_data in pages.values():
                if "extract" in page_data:
                    return page_data["extract"]
            return f"No Wikipedia article found for: {topic}"
        finally:
            client.close()
    except Exception as exc:
        return f"Wikipedia lookup failed: {exc}"