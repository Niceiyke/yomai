"""Tests for the eval harness: dataset, metrics, judge, runner, report."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest


class TestEvalDataset:
    """Dataset loading and validation."""

    def test_load_json_dataset(self) -> None:
        from yomai.eval.dataset import load_dataset

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(
                {
                    "name": "test_ds",
                    "description": "Desc",
                    "cases": [
                        {"name": "c1", "message": "What is 2+2?", "expected_output": "4"},
                        {"name": "c2", "message": "hello"},
                    ],
                },
                f,
            )
            f.flush()
            ds = load_dataset(f.name)
            Path(f.name).unlink()

        assert ds.name == "test_ds"
        assert ds.description == "Desc"
        assert len(ds.cases) == 2
        assert ds.cases[0].name == "c1"
        assert ds.cases[0].expected_output == "4"

    def test_load_json_list_format(self) -> None:
        from yomai.eval.dataset import load_dataset

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump(
                [
                    {"message": "hello"},
                    {"message": "world"},
                ],
                f,
            )
            f.flush()
            ds = load_dataset(f.name)
            Path(f.name).unlink()

        assert len(ds.cases) == 2
        assert ds.cases[0].message == "hello"

    def test_load_yaml_dataset(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("yaml not installed")

        from yomai.eval.dataset import load_dataset

        content = """name: ds
cases:
  - name: c1
    message: test
    expected_tools:
      - name: weather
        args: {city: Paris}
"""
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
            f.write(content)
            f.flush()
            ds = load_dataset(f.name)
            Path(f.name).unlink()

        assert ds.name == "ds"
        assert len(ds.cases) == 1
        assert ds.cases[0].expected_tools[0]["name"] == "weather"

    def test_eval_case_defaults(self) -> None:
        from yomai.eval.dataset import EvalCase

        case = EvalCase(message="hello")
        assert case.name == ""
        assert case.expected_output is None
        assert case.expected_tools == []
        assert case.forbidden_tools == []


class TestEvalMetrics:
    """Metric computation functions."""

    def test_accuracy_match(self) -> None:
        from yomai.eval.metrics import compute_accuracy

        assert compute_accuracy("The weather is 72F", "72F")
        assert compute_accuracy("The weather is 72F", "snow") is False

    def test_accuracy_no_expected(self) -> None:
        from yomai.eval.metrics import compute_accuracy

        assert compute_accuracy("anything", None)

    def test_tool_accuracy_full_match(self) -> None:
        from yomai.eval.metrics import compute_tool_accuracy

        assert (
            compute_tool_accuracy(
                [{"name": "weather", "args": {"city": "Paris"}}],
                [{"name": "weather", "args": {"city": "Paris"}}],
            )
            == 1.0
        )

    def test_tool_accuracy_wrong_name(self) -> None:
        from yomai.eval.metrics import compute_tool_accuracy

        assert (
            compute_tool_accuracy(
                [{"name": "search", "args": {}}],
                [{"name": "weather", "args": {}}],
            )
            == 0.0
        )

    def test_tool_accuracy_no_expected(self) -> None:
        from yomai.eval.metrics import compute_tool_accuracy

        assert compute_tool_accuracy([{"name": "x", "args": {}}], []) == 1.0

    def test_eval_metrics_properties(self) -> None:
        from yomai.eval.metrics import EvalMetrics

        m = EvalMetrics(total=2, passed=1, failed=1, judge_scores=[0.8, 0.6], latencies_ms=[100, 200])
        assert m.total == 2
        assert m.passed == 1
        assert m.avg_judge_score == 0.7
        assert m.avg_latency_ms == 150.0

    def test_percentile_latency(self) -> None:
        from yomai.eval.metrics import EvalMetrics

        m = EvalMetrics(latencies_ms=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100])
        assert m.p50_latency_ms() == 60.0
        assert m.p95_latency_ms() >= 95.0


class TestJudge:
    """Judge LLM scoring."""

    @pytest.mark.asyncio
    async def test_judge_exact_match(self) -> None:
        from yomai.eval.judge import judge_score

        score, reason = await judge_score("test", "hello world", expected="hello")
        assert score == 1.0
        assert "match" in reason.lower()

    @pytest.mark.asyncio
    async def test_judge_no_info(self) -> None:
        from yomai.eval.judge import judge_score

        score, reason = await judge_score("test", "hello world")
        assert score == 0.5


class TestEvalReport:
    """Report formatting."""

    def test_format_terminal(self) -> None:
        from yomai.eval.metrics import EvalMetrics
        from yomai.eval.report import format_terminal

        m = EvalMetrics(total=3, passed=2, failed=1, judge_scores=[0.9, 0.8, 0.3])
        report = format_terminal(m)
        assert "2/3" in report or "2" in report
        assert "Yomai Evaluation Report" in report

    def test_format_json(self) -> None:
        from yomai.eval.metrics import EvalMetrics
        from yomai.eval.report import format_json

        m = EvalMetrics(total=1, passed=1)
        report = format_json(m)
        parsed = json.loads(report)
        assert parsed["total"] == 1
        assert parsed["passed"] == 1

    def test_format_html(self) -> None:
        from yomai.eval.metrics import EvalMetrics
        from yomai.eval.report import format_html

        m = EvalMetrics(total=1, passed=1)
        report = format_html(m)
        assert "<!DOCTYPE html>" in report
        assert "Yomai Evaluation Report" in report
