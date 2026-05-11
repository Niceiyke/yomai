from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from yomai.exceptions import YomaiConfigError


class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    api_key: str = Field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY", ""))
    base_url: str | None = Field(default_factory=lambda: os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("OPENAI_BASE_URL"))
    max_tokens: int = 1024
    cost_per_token: dict[str, float] = Field(
        default_factory=lambda: {"input": 0.000003, "output": 0.000015}
    )


class MemoryConfig(BaseModel):
    backend: str = "dict"
    ttl_hours: int = 24
    max_messages: int = 20

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        if value != "dict":
            raise YomaiConfigError(
                f"Memory backend {value!r} is not available in V1.",
                hint="Redis backend ships in V2.",
                docs="https://yomai.dev/roadmap",
            )
        return value


class AgentConfig(BaseModel):
    max_tool_calls: int = 10
    timeout_secs: int = 120


class StreamingConfig(BaseModel):
    heartbeat_secs: int = 15
    max_duration_secs: int = 300


class DevConfig(BaseModel):
    ui: bool = True
    log_usage: bool = True
    reload: bool = True


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    dev: DevConfig = Field(default_factory=DevConfig)
