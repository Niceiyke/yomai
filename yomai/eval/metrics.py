"""Metric computation for agent evaluation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalMetrics:
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    accuracy: float = 0.0
    tool_accuracy: float = 0.0
    judge_scores: list[float] = field(default_factory=list)
    latencies_ms: list[float] = field(default_factory=list)
    tokens_input: list[int] = field(default_factory=list)
    tokens_output: list[int] = field(default_factory=list)
    costs_usd: list[float] = field(default_factory=list)
    per_case: list[dict[str, Any]] = field(default_factory=list)

    @property
    def avg_judge_score(self) -> float:
        if not self.judge_scores:
            return 0.0
        return sum(self.judge_scores) / len(self.judge_scores)

    @property
    def avg_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return sum(self.latencies_ms) / len(self.latencies_ms)

    @property
    def total_cost(self) -> float:
        return sum(self.costs_usd)

    @property
    def total_tokens_input(self) -> int:
        return sum(self.tokens_input)

    @property
    def total_tokens_output(self) -> int:
        return sum(self.tokens_output)

    def p50_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = len(sorted_lat) // 2
        return sorted_lat[idx]

    def p95_latency_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_lat = sorted(self.latencies_ms)
        idx = int(len(sorted_lat) * 0.95)
        return sorted_lat[min(idx, len(sorted_lat) - 1)]


def compute_accuracy(actual: str, expected: str | None) -> bool:
    if expected is None:
        return True
    return expected.lower().strip() in actual.lower().strip()


def compute_tool_accuracy(
    actual_tools: list[dict[str, Any]],
    expected_tools: list[dict[str, Any]],
) -> float:
    if not expected_tools:
        return 1.0
    if not actual_tools:
        return 0.0
    matched = 0
    for expected in expected_tools:
        exp_name = expected.get("name", "")
        exp_args = expected.get("args", {})
        for actual in actual_tools:
            act_name = actual.get("name", "")
            act_args = actual.get("args", {})
            if exp_name == act_name and (not exp_args or _args_subset(exp_args, act_args)):
                matched += 1
                break
    return matched / len(expected_tools)


def _args_subset(expected: dict[str, Any], actual: dict[str, Any]) -> bool:
    for key, val in expected.items():
        if key not in actual:
            return False
        if str(actual[key]).lower() != str(val).lower():
            return False
    return True
