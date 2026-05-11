"""Summarize a block of text or a URL."""
from __future__ import annotations

import httpx

from yomai import tool


@tool
def summarize_text(text: str, max_length: int = 200) -> str:
    """Summarize a piece of text.

    Args:
        text: The text content to summarize.
        max_length: Maximum character length of summary (default 200).
    """
    if not text or not text.strip():
        return "Nothing to summarize."

    sentences = text.replace("! ", ". ").replace("? ", ". ").split(". ")
    if len(sentences) <= 2:
        return text[:max_length]

    # Simple extractive summary: take first ~3 sentences
    summary = ". ".join(sentences[:3]).strip()
    if len(summary) > max_length:
        summary = summary[:max_length].rsplit(" ", 1)[0] + "..."
    return summary


@tool
def fetch_url(url: str) -> str:
    """Fetch the raw text content from a URL.

    Args:
        url: A valid HTTP(S) URL to fetch.
    """
    try:
        resp = httpx.get(url, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        # Extract visible text from HTML
        html = resp.text
        text = _strip_html(html)
        # Limit to first 3000 chars to avoid token bloat
        return text[:3000].strip()
    except Exception as exc:
        return f"Could not fetch URL: {exc}"


def _strip_html(html: str) -> str:
    """Remove HTML tags and decode entities."""
    import re
    # Remove script and style tags
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Decode common HTML entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text