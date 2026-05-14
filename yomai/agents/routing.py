"""Agent-to-agent routing: one agent calling another as a tool.

Provides an ``AgentTool`` that wraps a registered agent function as a
``@tool``-compatible callable, enabling multi-agent orchestration where
one agent can delegate subtasks to other agents via the LLM tool loop.

Cycle detection and depth limits prevent infinite recursion.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from yomai.core.app import Yomai


class AgentCallError(Exception):
    pass


class CycleDetected(AgentCallError):
    pass


class MaxDepthExceeded(AgentCallError):
    pass


def _make_agent_tool(
    agent_fn: Any,
    agent_name: str,
    *,
    description: str = "",
    max_depth: int = 5,
    call_stack: list[str] | None = None,
) -> Any:
    signature = inspect.signature(agent_fn)
    params = list(signature.parameters.values())

    extra_params: list[inspect.Parameter] = []
    for p in params:
        if p.name == "message" or p.name == "session_id" or p.name == "runner" or p.name == "request":
            continue
        else:
            extra_params.append(p)

    async def agent_as_tool(message: str = "", **kwargs: Any) -> str:
        call_stack_list = list(call_stack or [])
        if agent_name in call_stack_list:
            raise CycleDetected(f"Cycle detected: {' → '.join(call_stack_list)} → {agent_name}")
        if len(call_stack_list) >= max_depth:
            raise MaxDepthExceeded(f"Max agent call depth ({max_depth}) exceeded: {' → '.join(call_stack_list)}")

        app: Yomai = getattr(agent_fn, "_yomai_app", None)
        if app is None:
            return f"Agent {agent_name!r} is not attached to a Yomai app. Make sure it is decorated with @app.agent."

        provider = app._build_provider()
        tools = getattr(agent_fn, "_yomai_tools", [])
        agent_config = getattr(agent_fn, "_yomai_agent_config", app.config.agent)
        llm_config = app.config.llm
        system = getattr(agent_fn, "_yomai_agent_system", "")

        from yomai.core.agent import AgentLoop

        new_stack = call_stack_list + [agent_name]
        if tools:
            wrapped_tools = []
            for t in tools:
                if hasattr(t, "_yomai_app"):
                    wrapped_tools.append(
                        _make_agent_tool(
                            t,
                            getattr(t, "__name__", "sub_agent"),
                            max_depth=max_depth,
                            call_stack=new_stack,
                        )
                    )
                else:
                    wrapped_tools.append(t)
        else:
            wrapped_tools = tools

        prompt = message
        if kwargs:
            prompt = f"{message}\n\nAdditional context: {kwargs!r}"

        history: list[dict[str, Any]] = []
        loop = AgentLoop(provider, wrapped_tools, agent_config, llm_config, session_id=f"sub_{agent_name}")
        result_parts: list[str] = []

        async for sse in loop.run(prompt, history=history, system=system):
            import json

            try:
                lines = sse.split("\n")
                data_str = ""
                for line in lines:
                    if line.startswith("data:"):
                        data_str = line.removeprefix("data:").strip() + "\n" + data_str
                data_str = data_str.strip()
                parsed = json.loads(data_str) if data_str else {}
                if parsed.get("type") == "chunk":
                    result_parts.append(str(parsed.get("content", "")))
            except Exception:
                continue

        return "".join(result_parts) if result_parts else loop.last_reply

    tool_name = f"call_{agent_name}"
    agent_as_tool.__name__ = tool_name
    agent_as_tool.__doc__ = (
        description or f"Delegate a task to the {agent_name} agent. Provide a message with instructions."
    )

    params_desc = ""
    if extra_params:
        lines: list[str] = []
        for p in extra_params:
            ann = getattr(p.annotation, "__name__", str(p.annotation))
            lines.append(f"  - {p.name}: {ann}")
        params_desc = "\n".join(lines)

    if params_desc:
        agent_as_tool.__doc__ += f"\n\nAdditional parameters:\n{params_desc}"

    # Attach schema for tool registration
    agent_as_tool.schema = {
        "description": agent_as_tool.__doc__ or "",
        "properties": {
            "message": {
                "type": "string",
                "description": "The message/prompt to send to the agent",
            },
            **{
                p.name: {
                    "type": "string",
                    "description": f"Parameter: {p.name}",
                }
                for p in extra_params
            },
        },
        "required": ["message"],
    }
    agent_as_tool.tool_name = tool_name
    agent_as_tool._yomai_app = getattr(agent_fn, "_yomai_app", None)

    return agent_as_tool


def agent_tool(agent_fn: Any, *, name: str = "", description: str = "", max_depth: int = 5) -> Any:
    if not name:
        name = getattr(agent_fn, "__name__", "agent")
    return _make_agent_tool(agent_fn, name, description=description, max_depth=max_depth)


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}
        self._tool_cache: dict[str, Any] = {}

    def register(self, name: str, agent_fn: Any) -> None:
        if name in self._agents:
            existing_path = getattr(self._agents[name], "_yomai_path", "")
            new_path = getattr(agent_fn, "_yomai_path", "")
            if existing_path != new_path:
                raise AgentCallError(f"Agent {name!r} already registered at {existing_path}")
        self._agents[name] = agent_fn

    def get_agent(self, name: str) -> Any | None:
        return self._agents.get(name)

    def as_tool(self, name: str, *, max_depth: int = 5) -> Any:
        if name in self._tool_cache:
            return self._tool_cache[name]
        agent_fn = self._agents.get(name)
        if agent_fn is None:
            raise AgentCallError(f"Agent {name!r} not found in registry")
        tool = _make_agent_tool(agent_fn, name, max_depth=max_depth)
        self._tool_cache[name] = tool
        return tool

    def list_agents(self) -> list[str]:
        return list(self._agents.keys())

    def detect_cycles(self) -> list[list[str]]:
        graph: dict[str, set[str]] = {}
        for name, fn in self._agents.items():
            sub_agents = getattr(fn, "_yomai_tools", [])
            graph[name] = set()
            for tool in sub_agents:
                agent_name = getattr(tool, "__name__", "")
                for reg_name in self._agents:
                    if reg_name == agent_name or agent_name == f"call_{reg_name}":
                        graph[name].add(reg_name)

        cycles: list[list[str]] = []
        visited: set[str] = set()
        path: list[str] = []

        def dfs(node: str) -> None:
            if node in path:
                cycle_start = path.index(node)
                cycles.append(path[cycle_start:] + [node])
                return
            if node in visited:
                return
            visited.add(node)
            path.append(node)
            for neighbor in graph.get(node, set()):
                dfs(neighbor)
            path.pop()

        for node in graph:
            dfs(node)

        return cycles
