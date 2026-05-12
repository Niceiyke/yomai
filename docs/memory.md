# Memory

Yomai includes two V1 memory backends:

- `sqlite` — default, persists to `yomai_sessions.db`.
- `dict` — in-process memory for tests and small development apps.

Session behavior:

- Session ID comes from `X-Session-Id`.
- If absent, Yomai generates a UUID and returns it in `X-Session-Id`.
- The last `MemoryConfig.max_messages` messages are kept per session.
- `MemoryConfig.ttl_hours` evicts sessions older than the configured TTL. Set `ttl_hours=0` to disable TTL eviction.

```python
from yomai.config import MemoryConfig

app = Yomai(memory=MemoryConfig(
    backend="sqlite",
    db_path="yomai_sessions.db",
    max_messages=20,
    ttl_hours=24,
))
```

Security note: session IDs are bearer identifiers. Any caller who knows a session ID can continue that session unless you add authentication or signed session middleware around the app.

SQLite note: the SQLite backend enables WAL mode and a busy timeout, but it is still intended for simple single-node deployments. Use a custom `MemoryBackend` for larger multi-node production systems.
