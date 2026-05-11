from __future__ import annotations

from yomai import Yomai
from yomai.config import LLMConfig

app = Yomai(llm=LLMConfig(provider="anthropic"))


@app.agent("/chat")
async def chat(message: str, session_id: str) -> None:
    pass
