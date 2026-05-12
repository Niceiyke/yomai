#!/bin/bash
# Production smoke test - quick sanity check

set -e

SERVER_PORT=${1:-8000}
SERVER_URL="http://127.0.0.1:$SERVER_PORT"

echo "=== Production Smoke Test ==="
echo "Testing server at $SERVER_URL"

# Check health
echo -e "\n[1/7] Health check..."
curl -s "$SERVER_URL/__yomai__/health" | grep -q "ok" && echo "✓ Health OK" || echo "✗ Health failed"

# Check metrics
echo -e "\n[2/7] Metrics endpoint..."
curl -s "$SERVER_URL/metrics" | grep -q "requests_total" && echo "✓ Metrics OK" || echo "✗ Metrics failed"

# Check OpenAPI schema
echo -e "\n[3/7] OpenAPI schema..."
curl -s "$SERVER_URL/__yomai__/openapi.json" | grep -q '"/research"' && echo "✓ Schema OK" || echo "✗ Schema failed"

# Test agent endpoint (with mock response since no real API key)
echo -e "\n[4/7] Agent endpoint..."
response=$(curl -s -X POST "$SERVER_URL/research" \
  -H "Content-Type: application/json" \
  -H "X-Session-Id: smoke-test" \
  -d '{"message":"hello"}' \
  --max-time 10)
echo "$response" | head -c 200 | grep -q "event:" && echo "✓ Agent OK" || echo "✗ Agent failed"

# Test workflow endpoint
echo -e "\n[5/7] Workflow endpoint..."
curl -s -X POST "$SERVER_URL/batch-research" \
  -H "Content-Type: application/json" \
  -d '{"topics":["test"]}' | grep -q "job_id" && echo "✓ Workflow OK" || echo "✗ Workflow failed"

# Test session endpoint
echo -e "\n[6/7] Session endpoint..."
curl -s "$SERVER_URL/sessions/smoke-session" | grep -q "session_id" && echo "✓ Session OK" || echo "✗ Session failed"

# Check v2 routes
echo -e "\n[7/7] V2 routes..."
curl -s "$SERVER_URL/__yomai__/openapi.json" | grep -q '"/v2/research"' && echo "✓ V2 OK" || echo "✗ V2 failed"

echo -e "\n=== Smoke Test Complete ==="