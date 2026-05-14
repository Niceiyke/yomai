"""Eval runner that executes agents against datasets and collects results."""

from __future__ import annotations

import time
from typing import Any

from yomai.eval.dataset import EvalCase, EvalDataset
from yomai.eval.judge import judge_score
from yomai.eval.metrics import EvalMetrics, compute_accuracy, compute_tool_accuracy
from yomai.testing.client import YomaiTestClient


class EvalRunner:
    def __init__(
        self,
        client: YomaiTestClient,
        dataset: EvalDataset,
        *,
        judge_fn: Any = None,
        verbose: bool = False,
    ) -> None:
        self.client = client
        self.dataset = dataset
        self.judge_fn = judge_fn
        self.verbose = verbose

    async def run(self) -> EvalMetrics:
        metrics = EvalMetrics()
        for i, case in enumerate(self.dataset.cases):
            case_name = case.name or f"case_{i}"
            if self.verbose:
                print(f"  [{i + 1}/{len(self.dataset.cases)}] {case_name}...", end=" ", flush=True)

            try:
                result = await self._run_case(case)
                metrics.per_case.append(result)
                metrics.total += 1
                if result.get("passed", False):
                    metrics.passed += 1
                elif result.get("error"):
                    metrics.errors += 1
                else:
                    metrics.failed += 1
                if self.verbose:
                    status = "PASS" if result.get("passed") else ("ERR" if result.get("error") else "FAIL")
                    print(f"{status} (score={result.get('judge_score', 0):.2f})")
            except Exception as exc:
                metrics.total += 1
                metrics.errors += 1
                metrics.per_case.append({"name": case_name, "error": str(exc)})
                if self.verbose:
                    print(f"ERROR: {exc}")

        if metrics.total > 0:
            metrics.accuracy = metrics.passed / metrics.total
        return metrics

    async def _run_case(self, case: EvalCase) -> dict[str, Any]:
        start = time.monotonic()
        message = case.message if isinstance(case.message, str) else case.message
        extra: dict[str, Any] = {}
        if not isinstance(case.message, str):
            extra["message"] = case.message

        try:
            events = await self.client.get_events(
                path="/chat",
                message=message if isinstance(message, str) else "multi-modal",
                session_id=case.session_id,
                extra_body=extra if extra else None,
            )
        except Exception as exc:
            return {"name": case.name, "error": str(exc), "latency_ms": 0}

        latency_ms = int((time.monotonic() - start) * 1000)

        chunks = [str(e.get("content", "")) for e in events if e.get("type") == "chunk"]
        response = "".join(chunks)

        tools_called: list[dict[str, Any]] = []
        for e in events:
            if e.get("type") == "tool_start":
                tools_called.append({"name": e.get("name", ""), "args": e.get("args", {})})

        usage_events = [e for e in events if e.get("type") == "usage"]
        input_tokens = usage_events[-1].get("input_tokens", 0) if usage_events else 0
        output_tokens = usage_events[-1].get("output_tokens", 0) if usage_events else 0
        cost_usd = usage_events[-1].get("cost_usd", 0.0) if usage_events else 0.0

        text_match = compute_accuracy(response, case.expected_output)
        tool_match = compute_tool_accuracy(tools_called, case.expected_tools)

        forbidden_hit = any(t.get("name") in case.forbidden_tools for t in tools_called)

        judge_score_val, judge_reasoning = await judge_score(
            question=_message_str(case.message),
            response=response,
            expected=case.expected_output,
            rubric=case.rubric,
            judge_fn=self.judge_fn,
        )

        passed = text_match and tool_match >= 0.5 and not forbidden_hit
        if case.min_tokens is not None and output_tokens < case.min_tokens:
            passed = False

        return {
            "name": case.name,
            "response": response[:500],
            "expected": case.expected_output,
            "tools_called": tools_called,
            "expected_tools": case.expected_tools,
            "text_match": text_match,
            "tool_accuracy": tool_match,
            "forbidden_hit": forbidden_hit,
            "judge_score": judge_score_val,
            "judge_reasoning": judge_reasoning,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "passed": passed,
            "error": None,
        }


def _message_str(message: str | list[dict[str, Any]]) -> str:
    if isinstance(message, str):
        return message
    for block in message:
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text", ""))
    return "[multi-modal]"
