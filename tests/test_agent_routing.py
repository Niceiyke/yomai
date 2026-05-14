"""Tests for agent-to-agent routing: AgentRegistry, agent_tool, cycles."""

from __future__ import annotations

import pytest


class TestAgentRegistry:
    """Agent registration and lookup."""

    def test_register_and_get(self) -> None:
        from yomai.agents.routing import AgentRegistry

        def my_agent(message: str, session_id: str) -> None: ...

        registry = AgentRegistry()
        registry.register("my_agent", my_agent)
        assert registry.get_agent("my_agent") is my_agent
        assert registry.get_agent("nope") is None

    def test_duplicate_register_diff_path_raises(self) -> None:
        from yomai.agents.routing import AgentCallError, AgentRegistry

        def agent_a(message: str, session_id: str) -> None: ...
        def agent_b(message: str, session_id: str) -> None: ...

        setattr(agent_a, "_yomai_path", "/chat/a")
        setattr(agent_b, "_yomai_path", "/chat/b")

        registry = AgentRegistry()
        registry.register("my_agent", agent_a)
        with pytest.raises(AgentCallError):
            registry.register("my_agent", agent_b)

    def test_list_agents(self) -> None:
        from yomai.agents.routing import AgentRegistry

        def foo(message: str, session_id: str) -> None: ...
        def bar(message: str, session_id: str) -> None: ...

        registry = AgentRegistry()
        registry.register("foo", foo)
        registry.register("bar", bar)
        agents = registry.list_agents()
        assert "foo" in agents
        assert "bar" in agents

    def test_as_tool_creates_callable(self) -> None:
        from yomai.agents.routing import AgentRegistry

        def my_agent(message: str, session_id: str) -> None: ...

        registry = AgentRegistry()
        registry.register("my_agent", my_agent)

        tool = registry.as_tool("my_agent")
        assert callable(tool)
        assert getattr(tool, "tool_name", None) == "call_my_agent"

    def test_as_tool_unknown_raises(self) -> None:
        from yomai.agents.routing import AgentCallError, AgentRegistry

        registry = AgentRegistry()
        with pytest.raises(AgentCallError, match="not found"):
            registry.as_tool("nonexistent")

    def test_detect_cycles_no_cycles(self) -> None:
        from yomai.agents.routing import AgentRegistry

        def a(message: str, session_id: str) -> None: ...
        def b(message: str, session_id: str) -> None: ...

        setattr(a, "_yomai_tools", [])
        setattr(b, "_yomai_tools", [])

        registry = AgentRegistry()
        registry.register("a", a)
        registry.register("b", b)
        cycles = registry.detect_cycles()
        assert len(cycles) == 0


class TestAgentTool:
    """agent_tool wrapping."""

    def test_agent_tool_basic(self) -> None:
        from yomai.agents.routing import agent_tool

        def my_agent(message: str, session_id: str) -> None: ...

        tool = agent_tool(my_agent, name="helper")
        assert callable(tool)
        assert getattr(tool, "tool_name", None) == "call_helper"
        tool_schema = getattr(tool, "schema", {})
        assert isinstance(tool_schema, dict)
        assert "message" in tool_schema["properties"]
        assert "message" in tool_schema["required"]

    def test_agent_tool_with_extra_params(self) -> None:
        from yomai.agents.routing import agent_tool

        def my_agent(message: str, city: str, session_id: str) -> None: ...

        tool = agent_tool(my_agent, name="weather")
        assert "city" in getattr(tool, "schema", {})["properties"]

    def test_agent_tool_custom_name(self) -> None:
        from yomai.agents.routing import agent_tool

        def my_agent(message: str, session_id: str) -> None: ...

        tool = agent_tool(my_agent, name="custom_name", description="Custom agent")
        assert tool.tool_name == "call_custom_name"
        assert "Custom agent" in tool.__doc__

    @pytest.mark.asyncio
    async def test_agent_tool_max_depth_unattached(self) -> None:
        from yomai.agents.routing import agent_tool

        def my_agent(message: str, session_id: str) -> None: ...

        tool = agent_tool(my_agent, name="test", max_depth=3)

        result = await tool(message="test message")
        assert "not attached" in result
