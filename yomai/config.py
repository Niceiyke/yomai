from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from yomai import env
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
    def apply_provider_defaults(self) -> LLMConfig:
        if not self.api_key:
            env_key = "OPENAI_API_KEY" if self.provider == "openai" else "ANTHROPIC_API_KEY"
            self.api_key = getattr(env, env_key, "")
        if self.base_url is None:
            env_url = "OPENAI_BASE_URL" if self.provider == "openai" else "ANTHROPIC_BASE_URL"
            self.base_url = getattr(env, env_url, None)
        if self.provider == "openai" and self.model == "claude-sonnet-4-20250514":
            self.model = "gpt-4o-mini"
        return self


class MemoryConfig(BaseModel):
    backend: Literal["dict", "sqlite", "redis"] = "sqlite"
    ttl_hours: int = 24
    max_messages: int = 20
    db_path: str = "yomai_sessions.db"
    url: str | None = None
    prefix: str = "yomai:memory"

    @field_validator("backend")
    @classmethod
    def validate_backend(cls, value: str) -> str:
        if value not in ("dict", "sqlite", "redis"):
            raise YomaiConfigError(
                f"Unknown memory backend: {value!r}.",
                hint="Valid options: 'dict', 'sqlite', 'redis'.",
                docs="https://yomai.dev/roadmap",
            )
        return value

    @model_validator(mode="after")
    def apply_redis_defaults(self) -> MemoryConfig:
        if self.backend == "redis" and self.url is None:
            self.url = env.REDIS_URL
        return self


class AgentConfig(BaseModel):
    max_tool_calls: int = 10
    timeout_secs: int = 120


class StreamingConfig(BaseModel):
    heartbeat_secs: int = 15
    max_duration_secs: int = 300


class QueueConfig(BaseModel):
    backend: Literal["none", "inline", "swiftq"] = "none"
    url: str | None = None
    signing_key: str | None = None
    prefix: str = "yomai:swiftq"
    default_queue: str = "default"
    retries: int = 0
    retry_delay_secs: float = 0.0
    timeout_secs: int = 900
    job_ttl_secs: int = 86400
    event_ttl_secs: int = 86400

    @model_validator(mode="after")
    def apply_queue_defaults(self) -> QueueConfig:
        if self.backend == "swiftq" and self.url is None:
            self.url = env.REDIS_URL
        return self


class RateLimitConfig(BaseModel):
    requests_per_minute: int | None = None
    max_concurrent_per_session: int | None = None
    tokens_per_day: int | None = None


class BudgetConfig(BaseModel):
    max_tokens_per_request: int | None = None
    max_tokens_per_session: int | None = None
    max_cost_per_request: float | None = None
    max_cost_per_day: float | None = None
    on_exceeded: Literal["stop", "warn"] = "stop"


class DevConfig(BaseModel):
    ui: bool = True
    log_usage: bool = True
    reload: bool = True
    api_key: str = Field(default_factory=lambda: env.YOMAI_API_KEY)


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    streaming: StreamingConfig = Field(default_factory=StreamingConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    dev: DevConfig = Field(default_factory=DevConfig)
