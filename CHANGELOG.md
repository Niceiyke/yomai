# Changelog

## 0.1.0 - Unreleased

Initial pre-release baseline.

Features:
- Starlette-based `Yomai` ASGI app
- `@app.agent` routes with SSE streaming
- `@tool` decorator and sequential tool execution loop
- Anthropic and OpenAI-compatible providers
- In-process session memory
- `@app.workflow` and `WorkflowRunner`
- Testing utilities: `YomaiTestClient`, `mock_llm`, `capture_tools`
- Dev playground at `/__yomai__`
- CLI: `yomai new`, `yomai run`
- Typed package with `py.typed`
