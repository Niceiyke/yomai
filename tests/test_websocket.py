"""Tests for WebSocket event codec, codes and message parsing."""
from __future__ import annotations

import json


class TestWSCodec:
    """WebSocket JSON event codec."""

    def test_ws_chunk(self) -> None:
        from yomai.streaming.ws import ws_chunk
        data = json.loads(ws_chunk("hello"))
        assert data["type"] == "chunk"
        assert data["content"] == "hello"

    def test_ws_tool_start(self) -> None:
        from yomai.streaming.ws import ws_tool_start
        data = json.loads(ws_tool_start("weather", {"city": "NYC"}, "t1"))
        assert data["type"] == "tool_start"
        assert data["name"] == "weather"
        assert data["args"] == {"city": "NYC"}
        assert data["id"] == "t1"

    def test_ws_tool_end(self) -> None:
        from yomai.streaming.ws import ws_tool_end
        data = json.loads(ws_tool_end("t1", "72F", 142))
        assert data["type"] == "tool_end"
        assert data["result"] == "72F"
        assert data["duration_ms"] == 142

    def test_ws_tool_progress(self) -> None:
        from yomai.streaming.ws import ws_tool_progress
        data = json.loads(ws_tool_progress("t1", "loading..."))
        assert data["type"] == "tool_progress"
        assert data["message"] == "loading..."

    def test_ws_usage(self) -> None:
        from yomai.streaming.ws import ws_usage
        data = json.loads(ws_usage(342, 89, 0.0004))
        assert data["type"] == "usage"
        assert data["input_tokens"] == 342
        assert data["output_tokens"] == 89
        assert data["cost_usd"] == 0.0004

    def test_ws_done(self) -> None:
        from yomai.streaming.ws import ws_done
        data = json.loads(ws_done())
        assert data["type"] == "done"

    def test_ws_error(self) -> None:
        from yomai.streaming.ws import ws_error
        data = json.loads(ws_error("bad thing", "rate_limit"))
        assert data["type"] == "error"
        assert data["code"] == "rate_limit"

    def test_ws_step_start(self) -> None:
        from yomai.streaming.ws import ws_step_start
        data = json.loads(ws_step_start("classify", 1, 3))
        assert data["type"] == "step_start"
        assert data["name"] == "classify"
        assert data["index"] == 1
        assert data["of"] == 3

    def test_ws_step_done(self) -> None:
        from yomai.streaming.ws import ws_step_done
        data = json.loads(ws_step_done("classify", 1200))
        assert data["type"] == "step_done"
        assert data["duration_ms"] == 1200

    def test_ws_result(self) -> None:
        from yomai.streaming.ws import ws_result
        data = json.loads(ws_result('{"key": "val"}'))
        assert data["type"] == "result"
        assert data["content"] == '{"key": "val"}'

    def test_ws_interrupt(self) -> None:
        from yomai.streaming.ws import ws_interrupt
        data = json.loads(ws_interrupt("i1", "Approve?"))
        assert data["type"] == "interrupt"
        assert data["message"] == "Approve?"

    def test_ws_ping(self) -> None:
        from yomai.streaming.ws import ws_ping
        data = json.loads(ws_ping())
        assert data["type"] == "ping"

    def test_ws_graph_upsert(self) -> None:
        from yomai.streaming.ws import ws_graph_upsert
        data = json.loads(ws_graph_upsert("n1", "Node", "agent", "running", meta={"key": "val"}))
        assert data["type"] == "graph"
        assert data["action"] == "upsert"
        assert data["id"] == "n1"
        assert data["meta"]["key"] == "val"

    def test_ws_graph_edge(self) -> None:
        from yomai.streaming.ws import ws_graph_edge
        data = json.loads(ws_graph_edge("a", "b", "calls"))
        assert data["type"] == "graph"
        assert data["action"] == "edge"
        assert data["from"] == "a"
        assert data["to"] == "b"

    def test_ws_graph_update(self) -> None:
        from yomai.streaming.ws import ws_graph_update
        data = json.loads(ws_graph_update("n1", "done", meta={"dur": 100}))
        assert data["action"] == "update"
        assert data["status"] == "done"

    def test_ws_graph_clear(self) -> None:
        from yomai.streaming.ws import ws_graph_clear
        data = json.loads(ws_graph_clear())
        assert data["action"] == "clear"


class TestWSParseMessage:
    """WS client message parsing."""

    def test_parse_json_message(self) -> None:
        from yomai.streaming.ws import parse_ws_message
        result = parse_ws_message('{"type": "message", "content": "hello"}')
        assert result["type"] == "message"
        assert result["content"] == "hello"

    def test_parse_plain_text(self) -> None:
        from yomai.streaming.ws import parse_ws_message
        result = parse_ws_message("just some text")
        assert result["type"] == "message"
        assert result["content"] == "just some text"

    def test_parse_bytes(self) -> None:
        from yomai.streaming.ws import parse_ws_message
        result = parse_ws_message(b"bytes text")
        assert result["type"] == "message"
        assert result["content"] == "bytes text"

    def test_parse_ping_command(self) -> None:
        from yomai.streaming.ws import parse_ws_message
        result = parse_ws_message('{"type": "ping"}')
        assert result["type"] == "ping"

    def test_parse_stop_command(self) -> None:
        from yomai.streaming.ws import parse_ws_message
        result = parse_ws_message('{"type": "stop"}')
        assert result["type"] == "stop"
