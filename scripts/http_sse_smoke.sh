#!/usr/bin/env bash
set -euo pipefail

HOST=${HOST:-127.0.0.1}
PORT=${PORT:-18765}
URL="http://${HOST}:${PORT}"
LOG=${LOG:-/tmp/yomai_weather_agent.log}
OUT=${OUT:-/tmp/yomai_chat.sse}

rm -f "$LOG" "$OUT"
uv run uvicorn examples.weather_agent:app --host "$HOST" --port "$PORT" >"$LOG" 2>&1 &
PID=$!
cleanup() { kill "$PID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

for _ in $(seq 1 40); do
  if ! kill -0 "$PID" >/dev/null 2>&1; then
    echo "Server exited early. Log:" >&2
    cat "$LOG" >&2 || true
    exit 1
  fi
  if curl -fsS "$URL/__yomai__/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

echo "GET /__yomai__/health"
curl -fsS "$URL/__yomai__/health"
echo

echo "POST /chat"
curl -sS -N -X POST "$URL/chat" \
  -H 'Content-Type: application/json' \
  -d '{"message":"Use the get_weather tool for Tokyo, then answer briefly."}' | tee "$OUT"

grep -q 'event: tool_start' "$OUT"
grep -q 'event: tool_end' "$OUT"
grep -q 'event: chunk' "$OUT"
grep -q 'event: usage' "$OUT"
grep -q 'event: done' "$OUT"

echo
echo "HTTP SSE smoke ok"
