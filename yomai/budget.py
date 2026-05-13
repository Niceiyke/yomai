"""Budget enforcement for token and cost tracking.

Tracks cumulative token usage and estimated cost per session. Configured via
``BudgetConfig`` in the Yomai app.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from yomai.config import BudgetConfig
from yomai.log import get as _get_logger

_log = _get_logger("budget")


@dataclass
class BudgetState:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    requests: int = 0


class BudgetTracker:
    """Per-session budget tracking.

    Checks limits after each LLM response and returns whether the request
    should be stopped or continue with a warning.
    """

    def __init__(self, config: BudgetConfig) -> None:
        self.config = config
        self._sessions: dict[str, BudgetState] = {}
        self._daily_cost: float = 0.0
        self._daily_tokens: int = 0

    def check(
        self,
        session_id: str,
        tokens_in: int,
        tokens_out: int,
        cost_estimate: float,
    ) -> dict[str, Any]:
        """Check budget after an LLM response. Returns {'exceeded': True} if limits hit."""
        state = self._sessions.setdefault(session_id, BudgetState())
        state.tokens_in += tokens_in
        state.tokens_out += tokens_out
        state.cost_usd += cost_estimate
        state.requests += 1
        self._daily_cost += cost_estimate
        self._daily_tokens += tokens_in + tokens_out

        cfg = self.config
        result: dict[str, Any] = {"exceeded": False}

        # Per-request limits
        if cfg.max_tokens_per_request and (tokens_in + tokens_out) > cfg.max_tokens_per_request:
            result["exceeded"] = True
            result["reason"] = "max_tokens_per_request"
            result["limit"] = cfg.max_tokens_per_request
            result["actual"] = tokens_in + tokens_out

        # Per-session limits
        if cfg.max_tokens_per_session and state.tokens_in + state.tokens_out > cfg.max_tokens_per_session:
            result["exceeded"] = True
            result["reason"] = "max_tokens_per_session"
            result["limit"] = cfg.max_tokens_per_session
            result["actual"] = state.tokens_in + state.tokens_out

        # Per-request cost
        if cfg.max_cost_per_request and cost_estimate > cfg.max_cost_per_request:
            result["exceeded"] = True
            result["reason"] = "max_cost_per_request"
            result["limit"] = cfg.max_cost_per_request
            result["actual"] = round(cost_estimate, 6)

        # Daily cost
        if cfg.max_cost_per_day and self._daily_cost > cfg.max_cost_per_day:
            result["exceeded"] = True
            result["reason"] = "max_cost_per_day"
            result["limit"] = cfg.max_cost_per_day
            result["actual"] = round(self._daily_cost, 6)

        if result["exceeded"]:
            if cfg.on_exceeded == "stop":
                _log.warning("budget.exceeded %s session=%s", result.get("reason"), session_id, extra=result)
            else:
                _log.info("budget.warning %s session=%s", result.get("reason"), session_id, extra=result)
                result["exceeded"] = False

        return result

    def get_session_state(self, session_id: str) -> BudgetState:
        return self._sessions.get(session_id, BudgetState())

    def clear_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
