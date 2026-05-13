from __future__ import annotations

import json
import os
from typing import Any

from yomai.core.schemas import RouteMeta

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC_HTML = os.path.join(_THIS_DIR, "_static", "index.html")

_FALLBACK_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Yomai Playground</title>
<style>body{margin:40px;background:#0b0f17;color:#e5e7eb;font-family:sans-serif}
code{background:#1f2937;padding:2px 6px;border-radius:4px}</style></head>
<body><h1>Yomai Playground</h1>
<p>Static assets not built. Run <code>cd yomai/devui && npm install && npm run build</code> to compile the playground UI.</p>
</body></html>"""


def get_playground_html(routes: list[dict[str, Any]]) -> str:
    # Validate route metadata shape at the boundary
    validated = [RouteMeta.model_validate(r) for r in routes]
    if os.path.isfile(_STATIC_HTML):
        with open(_STATIC_HTML, encoding="utf-8") as f:
            html = f.read()
    else:
        return _FALLBACK_HTML

    routes_json = json.dumps([r.model_dump() for r in validated], ensure_ascii=False)
    return html.replace(
        '<script type="module"',
        f'<script>window.__ROUTES__ = {routes_json};</script><script type="module"',
    )
