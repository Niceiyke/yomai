"""Eval harness for testing agent correctness.

Provides dataset-driven evaluation with accuracy scoring, judge-LLM grading,
tool-call validation, and cost/latency tracking.
"""

from yomai.eval.dataset import EvalCase, EvalDataset, load_dataset
from yomai.eval.judge import judge_score
from yomai.eval.metrics import EvalMetrics, compute_accuracy, compute_tool_accuracy
from yomai.eval.report import format_html, format_json, format_terminal
from yomai.eval.runner import EvalRunner

__all__ = [
    "EvalRunner",
    "EvalDataset",
    "EvalCase",
    "EvalMetrics",
    "load_dataset",
    "judge_score",
    "compute_accuracy",
    "compute_tool_accuracy",
    "format_terminal",
    "format_json",
    "format_html",
]
