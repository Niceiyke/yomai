from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from yomai.exceptions import YomaiConfigError


class LLMConfig(BaseModel):
    provider: Literal["anthropic", "openai"] = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 1024
    cost_per_token: dict[str, float] = Field(
        default_factory=lambda: {"input": 0.000003, "output": 0.000015}
    )
    strip_reasoning: bool = False

    @model_validator(mode="after")
    def apply_provider_defaults(self) -> "LLMConfig":
        if not self.api_key:
            env_key = "OPENAI_API_KEY" if self.provider == "openai" else "ANTHROPIC_API_KEY"
            self.api_key = os.environ.get(env_key, "")
        if self.base_url is None:
            env_url = "OPENAI_BASE_URL" if self.provider == "openai" else "ANTHROPIC_BASE_URL"
            self.base_url = os.environ.get(env_url)
        if self.provider == "openai" and self.model == "claude-sonnet-4-20250514":
            self.model = "gpt-4o-mini"
        return self


class MemoryConfig(BaseModel):
    backend: Literal["dict", "sqlite"] = "sqlite"
    ttl_hours: int = 24
    max_messages: int = 20
    db_path: str = "yomai_sessions.db"

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        if value not in ("dict", "sqlite"):
            raise YomaiConfigError(
                f"Memory backend {value!r} is not available in V1.",
                hint="Valid options: 'dict', 'sqlite'.",
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
    api_key: str = Field(default_factory=lambda: os.environ.get("YOMAI_API_KEY", ""))


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    dev: DevConfig = Field(default_factory=DevConfig)
