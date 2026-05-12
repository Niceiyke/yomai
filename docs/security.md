# Security

Yomai provides minimal framework-level security controls. Public deployments should add application-level authentication and authorization.

## Route API keys

Set a global API key with `YOMAI_API_KEY` or `DevConfig(api_key=...)`:

```python
from yomai import Yomai
from yomai.config import DevConfig

app = Yomai(dev=DevConfig(api_key="global-secret"))
```

Requests must include:

```http
Authorization: Bearer global-secret
```

You can override auth per route:

```python
@app.agent("/public", api_key="")
async def public_chat(message: str) -> None:
    pass

@app.workflow("/admin", api_key="admin-secret")
async def admin_workflow() -> str:
    return "ok"
```

## Production metadata endpoints

When `YOMAI_ENV=production`, metadata endpoints such as `/__yomai__/routes` and `/__yomai__/openapi.json` require the global `YOMAI_API_KEY` if configured. Without a configured key, those endpoints are disabled.

## Signed sessions

Session IDs are bearer identifiers. If a user can guess or steal a session ID, they can continue that conversation. Use `SignedSessionMiddleware` to require signed `X-Session-Id` values:

```python
from yomai.middleware import SignedSessionMiddleware

app.add_middleware(SignedSessionMiddleware, secret="replace-me")
```

The signed value format is:

```text
session_id.signature
```

You can generate values with the middleware helper:

```python
mw = SignedSessionMiddleware(app, secret="replace-me")
signed = mw.sign("user-123-session")
```

For full public apps, signed sessions should be combined with user authentication, TLS, rate limiting, and provider budget controls.
