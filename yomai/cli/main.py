from __future__ import annotations

import importlib
import os
from pathlib import Path
from typing import Any

import typer

from yomai import env

app = typer.Typer(help="Yomai development CLI")


MAIN_PY = '''from yomai import Yomai, tool
from yomai.config import LLMConfig

app = Yomai(llm=LLMConfig(provider="anthropic"))

@tool
async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"

@app.agent("/chat", tools=[get_weather])
async def chat(message: str, session_id: str):
    pass
'''

TOOLS_PY = '''from yomai import tool

@tool
async def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"72°F and sunny in {city}"
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
def run(host: str = "0.0.0.0", port: int = 8000, reload: bool = True) -> None:
    """Run `main:app` with Yomai-aware output."""
    host = os.environ.get("HOST", host)
    port = int(os.environ.get("PORT", port))
    module = importlib.import_module("main")
    yomai_app: Any = module.app
    routes = getattr(yomai_app, "_routes_meta", [])
    typer.echo(f"\n  Yomai v0.1.0  ·  http://localhost:{port}")
    if env.YOMAI_ENV != "production":
        typer.echo(f"  Playground  →  http://localhost:{port}/__yomai__")
    typer.echo("\n  Routes")
    for route in routes:
        tools = ", ".join(route.get("tools", []))
        route_type = "AgentRoute" if route.get("type") == "agent" else "WorkflowRoute"
        typer.echo(f"    POST  {route.get('path')}     {route_type}   tools: [{tools}]")
    typer.echo("")

    import uvicorn

    uvicorn.run("main:app", host=host, port=port, reload=reload)


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
