# SSE Event Schema

All agent and workflow endpoints return `text/event-stream`.

Events:

```text
event: chunk
data: {"type":"chunk","content":"Hello"}

event: tool_start
data: {"type":"tool_start","name":"get_weather","args":{"city":"Tokyo"},"id":"t1"}

event: tool_end
data: {"type":"tool_end","id":"t1","result":"72°F","duration_ms":2}

event: usage
data: {"type":"usage","input_tokens":10,"output_tokens":5,"cost_usd":0.001}

event: done
data: {"type":"done"}
```

Workflows additionally emit `step_start`, `step_done`, and `result`.
