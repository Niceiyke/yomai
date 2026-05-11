from __future__ import annotations

import argparse
import asyncio
import os
import re
import time
from pathlib import Path

from yomai import tool
from yomai.config import AgentConfig, LLMConfig
from yomai.core.agent import AgentLoop
from yomai.llm.anthropic import AnthropicProvider
from yomai.llm.base import Done, TextChunk, ToolCall
from yomai.llm.openai import OpenAIProvider


def load_llm_md() -> dict[str, str]:
    text = Path("llm.md").read_text()
    values: dict[str, str] = {}
    for key in ["ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY"]:
        match = re.search(rf"^{key}=(.+)$", text, re.MULTILINE)
        if match:
            values[key] = match.group(1).strip().strip('"')
    model = re.search(r'^model="(.+)"$', text, re.MULTILINE)
    if model:
        values["MODEL"] = model.group(1)
    return values


def event_name(sse: str) -> str:
    for line in sse.splitlines():
        if line.startswith("event:"):
            return line.removeprefix("event:").strip()
    return "message"


def event_data(sse: str) -> str:
    for line in sse.splitlines():
        if line.startswith("data:"):
            return line.removeprefix("data:").strip()
    return "{}"


async def smoke_text(values: dict[str, str], provider_name: str) -> None:
    if provider_name == "anthropic":
        provider = AnthropicProvider(
            LLMConfig(
                provider="anthropic",
                api_key=values["ANTHROPIC_API_KEY"],
                base_url=values.get("ANTHROPIC_BASE_URL"),
                model=values.get("MODEL", "MiniMax-M2.7"),
                max_tokens=256,
            )
        )
    else:
        provider = OpenAIProvider(
            LLMConfig(
                provider="openai",
                api_key=values["OPENAI_API_KEY"],
                base_url=values.get("OPENAI_BASE_URL"),
                model=values.get("MODEL", "MiniMax-M2.7"),
                max_tokens=256,
            )
        )

    chunks: list[str] = []
    usage = None
    async for event in provider.stream(
        messages=[{"role": "user", "content": "Say only OK"}],
        tools=[],
        system="Be concise.",
    ):
        if isinstance(event, TextChunk):
            chunks.append(event.content)
        elif isinstance(event, Done):
            usage = event
        elif isinstance(event, ToolCall):
            raise AssertionError(f"unexpected tool call: {event}")
    text = "".join(chunks).strip()
    print(f"{provider_name}_text=", text)
    print(f"{provider_name}_usage=", usage)
    assert "OK" in text


@tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"


async def smoke_agent_loop(values: dict[str, str], provider_name: str, show_events: bool) -> None:
    if provider_name == "anthropic":
        cfg = LLMConfig(
            provider="anthropic",
            api_key=values["ANTHROPIC_API_KEY"],
            base_url=values.get("ANTHROPIC_BASE_URL"),
            model=values.get("MODEL", "MiniMax-M2.7"),
            max_tokens=256,
        )
        provider = AnthropicProvider(cfg)
    else:
        cfg = LLMConfig(
            provider="openai",
            api_key=values["OPENAI_API_KEY"],
            base_url=values.get("OPENAI_BASE_URL"),
            model=values.get("MODEL", "MiniMax-M2.7"),
            max_tokens=256,
        )
        provider = OpenAIProvider(cfg)

    loop = AgentLoop(provider, [get_weather], AgentConfig(max_tool_calls=3), cfg)
    events: list[str] = []
    started = time.monotonic()
    print(f"\n--- {provider_name} agent stream ---")
    async for sse in loop.run("Use the get_weather tool for Tokyo, then answer briefly.", [], ""):
        events.append(sse)
        if show_events:
            elapsed = time.monotonic() - started
            print(f"+{elapsed:06.2f}s {event_name(sse):<10} {event_data(sse)}", flush=True)
    joined = "".join(events)
    print(f"{provider_name}_agent_reply=", loop.last_reply.strip())
    assert "event: tool_start" in joined
    assert "event: tool_end" in joined
    assert loop.last_reply.strip()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Real LLM smoke test for Yomai")
    parser.add_argument("--provider", choices=["anthropic", "openai", "both"], default="both")
    parser.add_argument("--text", action="store_true", help="Also run plain text provider smoke tests")
    parser.add_argument("--no-events", action="store_true", help="Do not print live SSE events")
    args = parser.parse_args()

    values = load_llm_md()
    os.environ.update({k: v for k, v in values.items() if k != "MODEL"})
    providers = ["anthropic", "openai"] if args.provider == "both" else [args.provider]

    if args.text:
        for provider in providers:
            await smoke_text(values, provider)

    for provider in providers:
        await smoke_agent_loop(values, provider, show_events=not args.no_events)


if __name__ == "__main__":
    asyncio.run(main())
