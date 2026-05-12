# Changelog

## 0.1.0 - Unreleased

Initial pre-release baseline.

Features:
- Starlette-based `Yomai` ASGI app
- `@app.agent` routes with SSE streaming
- Agent handlers run before the LLM loop and can receive extra JSON body fields
- `@tool` decorator and sequential tool execution loop
- Route-scoped tool execution with signature binding and basic runtime argument checks
- Basic `Literal[...]` enum support in tool schemas
- Anthropic and OpenAI-compatible providers with provider-specific defaults
- Dict and SQLite session memory with max-message truncation and TTL eviction
- SQLite memory uses WAL mode and a busy timeout
- `@app.workflow` and `WorkflowRunner`
- Testing utilities: `YomaiTestClient`, `mock_llm`, `capture_tools`
- Dev playground at `/__yomai__`
- Production metadata endpoint auth when `YOMAI_API_KEY` is configured
- Per-route API key overrides for agents and workflows
- Pydantic validation/coercion for agent extra body fields and workflow inputs
- OpenAPI components for registered tool schemas
- Optional signed session middleware
- Production-safe streaming error messages
- CLI: `yomai new`, `yomai run`
- Typed package with `py.typed`
