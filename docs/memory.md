# Memory

V1 uses in-process `DictMemory`.

- Session ID comes from `X-Session-Id`.
- If absent, Yomai generates a UUID and returns it in `X-Session-Id`.
- The last `MemoryConfig.max_messages` messages are kept.
- Memory is lost on process restart.

```python
from yomai.config import MemoryConfig

app = Yomai(memory=MemoryConfig(max_messages=20))
```
