# Evaluation Harness

Test agent correctness with datasets, metric tracking, and judge-LLM scoring.

## Quick Start

Create an eval dataset file (`evals/support.json`):

```json
{
  "name": "support_agent",
  "description": "Tests for the support agent",
  "cases": [
    {
      "name": "simple_question",
      "message": "What is the return policy?",
      "expected_output": "30 days"
    },
    {
      "name": "tool_call_required",
      "message": "Cancel my order #12345",
      "expected_tools": [
        {"name": "cancel_order", "args": {"order_id": "12345"}}
      ]
    }
  ]
}
```

Run evaluation:

```bash
yomai eval evals/support.json --app main:app --verbose
```

## Dataset Format (YAML)

```yaml
name: support_tests
cases:
  - name: refund_check
    message: Can I get a refund?
    expected_output: refund policy
    expected_tools:
      - name: check_policy
        args: {type: refund}
    forbidden_tools:
      - admin_delete
    rubric: "Response should mention the 30-day refund window"
    min_tokens: 10

  - name: simple_query
    message: hello
```

## Case Fields

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Case identifier (shown in reports) |
| `message` | `str \| list` | User message or multi-modal content array |
| `expected_output` | `str` | Substring that must appear in the response |
| `expected_tools` | `list` | Tools that should be called with expected args |
| `forbidden_tools` | `list[str]` | Tools that must NOT be called |
| `rubric` | `str` | Judging criteria for LLM scoring |
| `min_tokens` | `int` | Minimum output tokens required |
| `session_id` | `str` | Override session for multi-turn tests |

## Output Formats

```bash
# Terminal table (default)
yomai eval evals/support.json

# JSON report
yomai eval evals/support.json --output json --output-file report.json

# HTML report
yomai eval evals/support.json --output html --output-file report.html
```

## Programmatic Use

```python
from yomai.eval import EvalRunner, load_dataset, format_terminal
from yomai.testing import YomaiTestClient, mock_llm

dataset = load_dataset("evals/support.yaml")

with mock_llm(responses=["Your order #12345 has been cancelled."]):
    client = YomaiTestClient(app)
    runner = EvalRunner(client, dataset, verbose=True)
    metrics = await runner.run()
    print(format_terminal(metrics))
    print(f"Accuracy: {metrics.accuracy:.1%}")
    print(f"Avg latency: {metrics.avg_latency_ms:.0f}ms")
```

## Metrics Tracked

- **Pass/Fail/Error counts**
- **Text accuracy** (substring match)
- **Tool call accuracy** (name + arg match)
- **Judge score** (LLM-based quality scoring, 0.0–1.0)
- **Latency** (ms, with P50/P95 percentiles)
- **Token usage** (input + output)
- **Cost** (USD, estimated)
