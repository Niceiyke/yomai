# WebSocket Transport

Yomai supports bidirectional real-time streaming over WebSocket as an alternative to SSE.

## Enabling WebSocket

```python
@app.agent("/chat", tools=[get_weather], transport="ws")
async def chat_agent(message: str, session_id: str):
    pass
```

The agent now accepts WebSocket connections at `ws://host/chat` instead of HTTP POST.

## Event Format

Events are JSON frames with the same type schema as SSE:

```json
{"type": "chunk", "content": "Hello"}
{"type": "tool_start", "name": "get_weather", "args": {"city": "Tokyo"}, "id": "t1"}
{"type": "tool_end", "id": "t1", "result": "72F", "duration_ms": 142}
{"type": "usage", "input_tokens": 342, "output_tokens": 89, "cost_usd": 0.0004}
{"type": "done"}
{"type": "error", "message": "Rate limit exceeded", "code": "rate_limited"}
{"type": "graph", "action": "upsert", "id": "n1", "label": "LLM", "kind": "llm", "status": "running"}
```

Heartbeat pings are sent every 15 seconds:

```json
{"type": "ping"}
```

## Client-to-Server Messages

The WebSocket connection is bidirectional. Send messages as JSON frames:

```json
{"type": "message", "content": "What is the weather?"}
{"type": "ping"}
{"type": "stop"}
```

Plain text strings are also accepted and treated as regular messages.

## Connection Lifecycle

1. Client connects → receives `{"type": "connected", "session_id": "..."}`
2. Client sends messages, server streams responses
3. Each response ends with `{"type": "done"}`
4. Client can send multiple messages on the same connection
5. Connection persists until client disconnects or sends `{"type": "stop"}`

## Session Persistence

Session IDs are preserved across messages on the same WebSocket connection. Memory accumulates across multiple exchanges within a session.

## JavaScript Client Example

```javascript
const ws = new WebSocket("ws://localhost:8000/chat");

ws.onopen = () => {
  ws.send(JSON.stringify({ type: "message", content: "Hello!" }));
};

ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  switch (data.type) {
    case "connected":
      console.log("Session:", data.session_id);
      break;
    case "chunk":
      process.stdout.write(data.content);
      break;
    case "tool_start":
      console.log(`\n🔧 ${data.name}(${JSON.stringify(data.args)})`);
      break;
    case "done":
      console.log("\n✓ Done");
      break;
  }
};
```
