"""Plugin support for Yomai.

Plugins are callables ``setup(app: Yomai) -> None`` that register custom
backends, middleware, hooks, or configuration when the app starts.

Usage::

    # my_plugin.py
    async def on_agent(event): ...
    def setup(app):
        app.hooks.on("agent.start", on_agent)

    # main.py
    from yomai import Yomai
    from my_plugin import setup
    app = Yomai(plugins=[setup])

Plugins can also be loaded by module name (string path)::

    app = Yomai(plugins=["my_package.my_plugin:setup"])

or with the ``@yomai.plugin`` decorator for discovery::

    from yomai import plugin

    @plugin
    def setup(app): ...
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

PluginSetup = Callable[..., Any]

_registry: list[PluginSetup] = []


def plugin(fn: PluginSetup) -> PluginSetup:
    """Decorator that registers a setup function for auto-discovery."""
    _registry.append(fn)
    return fn


def load_plugins(setups: list[PluginSetup | str] | None) -> list[PluginSetup]:
    """Resolve a mixed list of callables and module paths to callables."""
    resolved: list[PluginSetup] = []
    for setup in setups or []:
        if callable(setup):
            resolved.append(setup)
        elif isinstance(setup, str):
            if ":" in setup:
                module_name, attr = setup.rsplit(":", 1)
            else:
                module_name, attr = setup, "setup"
            mod = importlib.import_module(module_name)
            fn = getattr(mod, attr)
            if not callable(fn):
                raise ValueError(f"{setup} did not resolve to a callable")
            resolved.append(fn)
        else:
            raise TypeError(f"Expected callable or str, got {type(setup)}")
    return resolved
