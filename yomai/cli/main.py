from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

import typer

from yomai import env
from yomai._version import __version__ as _yomai_version

app = typer.Typer(help="Yomai development CLI")


MAIN_PY = '''from yomai import Yomai, tool, HookEvent
from yomai.config import LLMConfig, MemoryConfig, BudgetConfig, AgentConfig

app = Yomai(
    llm=LLMConfig(provider="anthropic"),
    memory=MemoryConfig(backend="sqlite", db_path="sessions.db"),
    agent=AgentConfig(max_tool_calls=5, timeout_secs=120),
    budgets=BudgetConfig(max_tokens_per_request=8000),
)

# ── Tools ──────────────────────────────────────────

@tool(cache_ttl=300)
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"

# ── Agents ─────────────────────────────────────────

@app.agent("/chat", tools=[get_weather], system="You are a helpful assistant.")
async def chat(message: str, session_id: str):
    """Handler runs before the LLM. Validate, load context, or return dict to
    override system prompt: return {"system": "...", "context": "..."}"""
    pass

# ── Workflows ──────────────────────────────────────

@app.workflow("/research")
async def research(topic: str, runner):
    """Orchestrate multi-step agent pipelines with shared state."""
    runner.state["topic"] = topic
    summary = await runner.step("summarize", chat, f"Summarize: {topic}")
    return summary

# ── Hooks ──────────────────────────────────────────

@app.on("agent.start")
async def on_agent_start(event: HookEvent):
    print(f"Agent started: {event.payload}")

@app.on("agent.error")
async def on_agent_error(event: HookEvent):
    print(f"Agent failed: {event.payload.get('error')}")

# ── Plugins ────────────────────────────────────────
# from my_plugin import setup
# app = Yomai(plugins=[setup])
'''

TOOLS_PY = '''from yomai import tool

@tool(cache_ttl=300)
async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"

@tool
async def search(query: str) -> str:
    """Search for information."""
    return f"Results for {query}: ..."

@tool
def calculate(expression: str) -> float:
    """Evaluate a mathematical expression."""
    import ast
    import operator as _op
    _OPS: dict[type, Any] = {
        ast.Add: _op.add, ast.Sub: _op.sub, ast.Mult: _op.mul,
        ast.Div: _op.truediv, ast.Pow: _op.pow, ast.USub: _op.neg,
    }
    def _eval(node: ast.AST) -> float:
        match node:
            case ast.Expression(body=body):
                return _eval(body)
            case ast.Constant(value=value):
                return float(value)
            case ast.BinOp(left=l, op=op, right=r):
                return _OPS[type(op)](_eval(l), _eval(r))
            case ast.UnaryOp(op=op, operand=o):
                return _OPS[type(op)](_eval(o))
            case _:
                raise ValueError(f"Unsupported expression: {expression!r}")
    return _eval(ast.parse(expression, mode="eval"))
'''

@app.command()
def new(project_name: str) -> None:
    """Scaffold a new Yomai project."""
    root = Path(project_name)
    root.mkdir(parents=True, exist_ok=False)
    (root / "main.py").write_text(MAIN_PY)
    (root / "tools.py").write_text(TOOLS_PY)
    (root / "requirements.txt").write_text("yomai\nuvicorn\nanthropic\n")
    (root / ".env.example").write_text("ANTHROPIC_API_KEY=your-key-here\n")
    typer.echo(f"Created {project_name}")


@app.command()
def run(
    app_path: str = "main:app",
    host: str = "0.0.0.0",
    port: int = 8000,
    reload: bool = True,
) -> None:
    """Run a Yomai app with dev server and playground.

    app_path format: module:attribute (e.g. 'main:app', 'app:app', 'myapp:server')
    """
    host = os.environ.get("HOST", host)
    port = int(os.environ.get("PORT", port))

    module_name, _, attr = app_path.partition(":")
    if not module_name or not attr:
        module_name = app_path
        attr = "app"

    # Ensure the current directory is on the import path
    import sys
    sys.path.insert(0, os.getcwd())

    # Auto-load .env if python-dotenv is available
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    module = importlib.import_module(module_name)
    yomai_app: Any = getattr(module, attr)
    routes = getattr(yomai_app, "_routes_meta", [])

    typer.echo(f"\n  Yomai v{_yomai_version}  ·  http://localhost:{port}")
    if env.YOMAI_ENV != "production":
        typer.echo(f"  Playground  →  http://localhost:{port}/__yomai__")
    typer.echo("\n  Routes")
    for route in routes:
        tools = ", ".join(route.get("tools", []))
        route_type = "AgentRoute" if route.get("type") == "agent" else "WorkflowRoute"
        typer.echo(f"    POST  {route.get('path')}     {route_type}   tools: [{tools}]")
    typer.echo("")

    import uvicorn

    uvicorn.run(f"{module_name}:{attr}", host=host, port=port, reload=reload)


@app.command()
def worker(
    app_path: str = "main:app",
    queue: str | None = None,
    concurrency: int = 1,
    burst: bool = False,
    with_scheduler: bool = False,
    worker_id: str | None = None,
) -> None:
    """Run a Yomai async workflow worker."""
    module_name, _, attr = app_path.partition(":")
    if not module_name or not attr:
        raise typer.BadParameter("app_path must look like 'module:app'")

    import sys
    sys.path.insert(0, os.getcwd())

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    module = importlib.import_module(module_name)
    yomai_app: Any = getattr(module, attr)
    if getattr(yomai_app.config.queue, "backend", "none") != "swiftq":
        raise typer.BadParameter("yomai worker requires QueueConfig(backend='swiftq')")
    backend = yomai_app._get_queue_backend()
    typer.echo(f"Starting Yomai worker for {app_path} queue={queue or yomai_app.config.queue.default_queue}")
    backend.work(
        queue=queue,
        concurrency=concurrency,
        burst=burst,
        with_scheduler=with_scheduler,
        worker_id=worker_id,
    )


@app.command()
def serve(
    app_path: str = "main:app",
    host: str = "0.0.0.0",
    port: int = 8000,
    workers: int = 1,
    proxy_headers: bool = False,
) -> None:
    """Production-grade server — multi-worker, no reload, proxy headers."""
    import uvicorn

    # Auto-load .env
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    host = os.environ.get("HOST", host)
    port = int(os.environ.get("PORT", port))
    workers = int(os.environ.get("YOMAI_WORKERS", workers))
    proxy_headers = os.environ.get("YOMAI_PROXY_HEADERS", str(proxy_headers)).lower() == "true"

    uvicorn.run(
        app_path,
        host=host,
        port=port,
        workers=workers,
        proxy_headers=proxy_headers,
        log_level="info",
        reload=False,
    )


@app.command()
def dev(
    app_path: str = "main:app",
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run with reload and playground enabled (alias for 'yomai run --reload')."""
    import sys
    sys.path.insert(0, os.getcwd())

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    host = os.environ.get("HOST", host)
    port = int(os.environ.get("PORT", port))

    module_name, _, attr = app_path.partition(":")
    if not module_name or not attr:
        module_name, attr = app_path, "app"

    module = importlib.import_module(module_name)
    yomai_app: Any = getattr(module, attr)
    routes = getattr(yomai_app, "_routes_meta", [])

    typer.echo(f"\n  Yomai v{_yomai_version}  ·  http://localhost:{port}")
    typer.echo(f"  Playground  →  http://localhost:{port}/__yomai__")
    typer.echo("\n  Routes")
    for route in routes:
        tools = ", ".join(route.get("tools", []))
        route_type = "AgentRoute" if route.get("type") == "agent" else "WorkflowRoute"
        typer.echo(f"    POST  {route.get('path')}     {route_type}   tools: [{tools}]")
    typer.echo("")

    import uvicorn
    uvicorn.run(f"{module_name}:{attr}", host=host, port=port, reload=True, log_level="info")


@app.command()
def deploy(
    app_path: str = "main:app",
    output: str = "Dockerfile",
    workers: int = 1,
) -> None:
    """Generate a Dockerfile for production deployment."""
    lines = [
        "# syntax=docker/dockerfile:1",
        "FROM python:3.12-slim",
        "WORKDIR /app",
        "COPY . .",
        "RUN pip install --no-cache-dir -r requirements.txt",
        "ENV YOMAI_ENV=production",
        "ENV HOST=0.0.0.0",
        "ENV PORT=8000",
        "EXPOSE 8000",
        f'CMD ["yomai", "serve", "{app_path}", "--workers", "{workers}", "--proxy-headers"]',
        "",
    ]
    dockerfile = "\n".join(lines)
    Path(output).write_text(dockerfile)
    typer.echo(f"Generated {output}")

    compose_path = Path("docker-compose.yml")
    if not compose_path.exists():
        compose_path.write_text(
            """# Generated by yomai deploy
services:
  app:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    restart: unless-stopped
"""
        )
        typer.echo("Generated docker-compose.yml")
    typer.echo("\n  docker build -t myapp .")
    typer.echo("  docker run -p 8000:8000 --env-file .env myapp")
