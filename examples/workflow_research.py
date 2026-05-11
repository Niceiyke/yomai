from __future__ import annotations

from yomai import Yomai
from yomai.config import LLMConfig
from yomai.workflow import WorkflowRunner

app = Yomai(llm=LLMConfig(provider="anthropic"))


@app.agent("/summarize")
async def summarize(message: str, session_id: str) -> None:
    pass


@app.workflow("/research")
async def research(topic: str, runner: WorkflowRunner) -> str:
    summary = await runner.step("summarize", summarize, f"Write a short research summary about {topic}")
    return summary
