# Testing

Yomai ships deterministic testing utilities.

```python
from yomai.testing import YomaiTestClient, mock_llm, MockToolCall, capture_tools

with mock_llm(["Hello"]):
    client = YomaiTestClient(app)
    text = await client.call("/chat", "Say hello")

with mock_llm([[MockToolCall("get_weather", {"city":"Tokyo"})], ["Sunny"]]):
    with capture_tools("72°F") as calls:
        await client.call("/chat", "weather")
```
