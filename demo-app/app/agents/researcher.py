"""Research assistant agent — searches the web, fetches URLs, and summarizes."""
from __future__ import annotations

from yomai import Yomai
from yomai.config import LLMConfig

from app.tools.search import web_search
from app.tools.summarize import fetch_url, summarize_text

app = Yomai(
    llm=LLMConfig(max_tokens=512),
)


@app.agent(
    "/research",
    tools=[web_search, fetch_url, summarize_text],
    system=(
        "You are a research assistant. Use the provided tools to answer questions thoroughly.\n"
        "When a user asks about something factual, search the web first.\n"
        "If you find a relevant URL, you may fetch it to get more detail.\n"
        "Always cite your sources with the URLs you found."
    ),
)
async def research(message: str, session_id: str) -> None:
    pass