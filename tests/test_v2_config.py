from __future__ import annotations

from yomai import Yomai
from yomai.config import BudgetConfig, LLMConfig, MemoryConfig, QueueConfig, RateLimitConfig


def test_v2_queue_config_defaults_redis_url() -> None:
    cfg = QueueConfig(backend="swiftq")
    assert cfg.backend == "swiftq"
    assert cfg.url == "redis://localhost:6379/0"
    assert cfg.prefix == "yomai:swiftq"


def test_v2_controls_config_attached_to_app() -> None:
    app = Yomai(
        llm=LLMConfig(api_key=""),
        memory=MemoryConfig(backend="dict", db_path="/unused"),
        queue=QueueConfig(backend="inline"),
        rate_limits=RateLimitConfig(requests_per_minute=60, max_concurrent_per_session=3),
        budgets=BudgetConfig(max_cost_per_request=0.10, on_exceeded="warn"),
    )
    assert app.config.queue.backend == "inline"
    assert app.config.rate_limits.requests_per_minute == 60
    assert app.config.budgets.max_cost_per_request == 0.10
    assert app.config.budgets.on_exceeded == "warn"


def test_v2_redis_memory_config_defaults_url() -> None:
    cfg = MemoryConfig(backend="redis")
    assert cfg.url == "redis://localhost:6379/0"
