"""Report generation for evaluation results."""

from __future__ import annotations

import json

from yomai.eval.metrics import EvalMetrics


def format_terminal(metrics: EvalMetrics) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  Yomai Evaluation Report")
    lines.append("=" * 60)
    lines.append(f"  Total cases:    {metrics.total}")
    lines.append(f"  Passed:         {metrics.passed} ({metrics.accuracy:.1%})")
    lines.append(f"  Failed:         {metrics.failed}")
    lines.append(f"  Errors:         {metrics.errors}")
    lines.append(f"  Avg judge:      {metrics.avg_judge_score:.2f}")
    lines.append(f"  Tool accuracy:  {metrics.tool_accuracy:.1%}")
    lines.append(f"  Avg latency:    {metrics.avg_latency_ms:.0f}ms")
    lines.append(f"  P50 latency:    {metrics.p50_latency_ms():.0f}ms")
    lines.append(f"  P95 latency:    {metrics.p95_latency_ms():.0f}ms")
    lines.append(f"  Total tokens in:  {metrics.total_tokens_input}")
    lines.append(f"  Total tokens out: {metrics.total_tokens_output}")
    lines.append(f"  Total cost:     ${metrics.total_cost:.4f}")
    lines.append("=" * 60)

    if metrics.per_case:
        lines.append("\n  Per-case results:")
        for case in metrics.per_case:
            name = case.get("name", "unnamed")
            passed = case.get("passed", False)
            error = case.get("error")
            score = case.get("judge_score", 0)
            latency = case.get("latency_ms", 0)
            status = "ERR" if error else ("PASS" if passed else "FAIL")
            lines.append(f"    [{status}] {name}  score={score:.2f}  latency={latency}ms")
            if error:
                lines.append(f"           error: {error}")

    return "\n".join(lines)


def format_json(metrics: EvalMetrics) -> str:
    return json.dumps(
        {
            "total": metrics.total,
            "passed": metrics.passed,
            "failed": metrics.failed,
            "errors": metrics.errors,
            "accuracy": metrics.accuracy,
            "tool_accuracy": metrics.tool_accuracy,
            "avg_judge_score": metrics.avg_judge_score,
            "avg_latency_ms": metrics.avg_latency_ms,
            "p50_latency_ms": metrics.p50_latency_ms(),
            "p95_latency_ms": metrics.p95_latency_ms(),
            "total_tokens_input": metrics.total_tokens_input,
            "total_tokens_output": metrics.total_tokens_output,
            "total_cost": metrics.total_cost,
            "per_case": [
                {k: v for k, v in case.items() if k not in ("response", "expected")} for case in metrics.per_case
            ],
        },
        indent=2,
    )


def format_html(metrics: EvalMetrics) -> str:
    cases_html = ""
    for case in metrics.per_case:
        name = case.get("name", "unnamed")
        passed = case.get("passed", False)
        error = case.get("error")
        score = case.get("judge_score", 0)
        lat = case.get("latency_ms", 0)
        status_color = "red" if error else ("green" if passed else "orange")
        error_html = f'<br><span style="color:red">{error}</span>' if error else ""
        cases_html += f"""
        <tr>
            <td style="color:{status_color};font-weight:bold">{"PASS" if passed else ("ERR" if error else "FAIL")}</td>
            <td>{name}</td>
            <td>{score:.2f}</td>
            <td>{lat}ms</td>
            <td>{case.get("input_tokens", 0)} / {case.get("output_tokens", 0)}</td>
            <td>${case.get("cost_usd", 0):.4f}</td>
            <td>{error_html}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Yomai Eval Report</title>
    <style>
        body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ padding: 8px 12px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background: #f5f5f5; }}
        .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin: 2rem 0; }}
        .card {{ background: #f9f9f9; border-radius: 8px; padding: 1rem; text-align: center; }}
        .card .value {{ font-size: 2rem; font-weight: bold; }}
        .card .label {{ color: #666; font-size: 0.85rem; }}
    </style>
</head>
<body>
    <h1>Yomai Evaluation Report</h1>
    <div class="summary">
        <div class="card"><div class="value">{metrics.passed}/{metrics.total}</div><div class="label">Passed</div></div>
        <div class="card"><div class="value">{metrics.accuracy:.1%}</div><div class="label">Accuracy</div></div>
        <div class="card"><div class="value">{metrics.avg_judge_score:.2f}</div><div class="label">Avg Judge Score</div></div>
        <div class="card"><div class="value">${metrics.total_cost:.4f}</div><div class="label">Total Cost</div></div>
    </div>
    <h2>Case Results</h2>
    <table>
        <thead>
            <tr><th>Status</th><th>Case</th><th>Judge</th><th>Latency</th><th>Tokens (in/out)</th><th>Cost</th><th>Info</th></tr>
        </thead>
        <tbody>{cases_html}</tbody>
    </table>
</body>
</html>"""
