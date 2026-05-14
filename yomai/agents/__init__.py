"""Agent-to-agent routing for multi-agent orchestration.

Enables one agent to call another as a tool via the LLM tool loop,
with automatic schema generation, cycle detection, and depth limiting.
"""

from yomai.agents.routing import AgentCallError, AgentRegistry, CycleDetected, MaxDepthExceeded, agent_tool

__all__ = [
    "AgentRegistry",
    "agent_tool",
    "AgentCallError",
    "CycleDetected",
    "MaxDepthExceeded",
]
