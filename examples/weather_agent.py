from __future__ import annotations

import os
from typing import Literal, cast

from yomai import Yomai, tool
from yomai.config import LLMConfig

provider = cast(Literal["anthropic", "openai"], os.environ.get("YOMAI_PROVIDER", "anthropic"))

if provider == "openai":
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL")
else:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL")

app = Yomai(
    llm=LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=os.environ.get("YOMAI_MODEL", "claude-sonnet-4-20250514"),
        max_tokens=256,
    )
)


@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"


@app.agent("/chat", tools=[get_weather])
async def chat(message: str, session_id: str) -> None:
    pass
