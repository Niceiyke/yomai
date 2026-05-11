from __future__ import annotations

import os

from yomai import Yomai, tool
from yomai.config import LLMConfig

app = Yomai(
    llm=LLMConfig(
        provider="openai",
        model=os.environ.get("YOMAI_MODEL", "gpt-4o"),
        api_key=os.environ.get("OPENAI_API_KEY", ""),
        base_url=os.environ.get("OPENAI_BASE_URL"),
    )
)


@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"


@app.agent("/chat", tools=[get_weather])
async def chat(message: str, session_id: str) -> None:
    pass
