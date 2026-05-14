"""Judge LLM scoring for agent evaluation."""

from __future__ import annotations

from typing import Any

_JUDGE_PROMPT = """You are an evaluator. You will be given an agent's response and optionally a rubric or expected output.
Score the response from 0.0 to 1.0 where:
- 1.0: Perfect. Fully satisfies the request and meets all criteria.
- 0.7-0.9: Good. Mostly correct with minor issues.
- 0.4-0.6: Mediocre. Partially correct but has significant gaps.
- 0.1-0.3: Poor. Mostly incorrect or irrelevant.
- 0.0: Completely wrong, empty, or harmful.

Return ONLY a JSON object with the format:
{"score": <float>, "reasoning": "<brief explanation>"}

Input:
Question: {question}
Expected output (optional): {expected}
Rubric (optional): {rubric}

Agent response:
{response}

Score:"""


async def judge_score(
    question: str,
    response: str,
    expected: str | None = None,
    rubric: str | None = None,
    *,
    judge_fn: Any = None,
) -> tuple[float, str]:
    if judge_fn is not None:
        prompt = _JUDGE_PROMPT.format(
            question=question,
            expected=expected or "N/A",
            rubric=rubric or "N/A",
            response=response[:4000],
        )
        try:
            result = await judge_fn(prompt)
            import json

            parsed = json.loads(result)
            return float(parsed.get("score", 0)), str(parsed.get("reasoning", ""))
        except Exception:
            return 0.0, "Judge failed"

    if expected and expected.lower().strip() in response.lower().strip():
        return 1.0, "Exact match"
    if rubric is None and expected is None:
        return 0.5, "No rubric or expected output provided"
    return 0.5, "No judge function provided; default score"
