# Testing

Yomai ships deterministic testing utilities.

```python
from yomai.testing import YomaiTestClient, mock_llm, MockToolCall, capture_tools

client = YomaiTestClient(app)

with mock_llm(["Hello"]):
    text = await client.call("/chat", "Say hello")

with mock_llm([[MockToolCall("get_weather", {"city":"Tokyo"})], ["Sunny"]]):
    with capture_tools("72°F") as calls:
        await client.call("/chat", "weather")
```

`YomaiTestClient.call()` and `.stream()` accept `extra_body` for agent parameters beyond `message`:

```python
text = await client.call(
    "/chat",
    "hello",
    session_id="s1",
    extra_body={"tone": "friendly"},
)
```

`mock_llm()` monkeypatches providers globally and is intended for simple deterministic tests. Avoid parallel tests that use different `mock_llm()` contexts at the same time.
