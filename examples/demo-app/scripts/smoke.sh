#!/bin/bash
# Smoke-test the demo app using a real LLM (requires .env or env vars set).

set -e

BASE_URL="${BASE_URL:-http://localhost:8000}"

echo "=== Health check ==="
curl -s "$BASE_URL/__yomai__/health"

echo ""
echo "=== OpenAPI schema ==="
curl -s "$BASE_URL/__yomai__/openapi.json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'Routes: {list(d[\"paths\"].keys())}')"

echo ""
echo "=== POST /research (streaming SSE) ==="
curl -s -X POST "$BASE_URL/research" \
  -H "Content-Type: application/json" \
  -d '{"query": "What is quantum computing?", "session_id": "test-001"}' \
  --no-buffer \
  | head -20