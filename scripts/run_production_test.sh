#!/bin/bash
# Production smoke test - comprehensive server tests

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DEMO_DIR="$PROJECT_ROOT/demo-app"

# Configuration
PORT=${PORT:-8000}
HOST="127.0.0.1"
URL="http://$HOST:$PORT"
PID_FILE="/tmp/yomai-prod-test.pid"
LOG_FILE="/tmp/yomai-prod-test.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

FAILED=0
PASSED=0

cleanup() {
    echo -e "\n${YELLOW}Cleaning up...${NC}"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            sleep 1
            kill -9 "$PID" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
    fi
    rm -f "$LOG_FILE"
}

trap cleanup EXIT

test_result() {
    local name="$1"
    local expected="$2"
    local actual="$3"
    
    if [ "$expected" = "$actual" ]; then
        echo -e "${GREEN}✓ $name${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ $name (expected $expected, got $actual)${NC}"
        ((FAILED++))
    fi
}

test_contains() {
    local name="$1"
    local haystack="$2"
    local needle="$3"
    
    if echo "$haystack" | grep -q "$needle"; then
        echo -e "${GREEN}✓ $name${NC}"
        ((PASSED++))
    else
        echo -e "${RED}✗ $name (did not find '$needle')${NC}"
        ((FAILED++))
    fi
}

# Build and install
echo -e "${BLUE}Building Yomai...${NC}"
cd "$PROJECT_ROOT"
uv run python -m build > /dev/null 2>&1

echo -e "${BLUE}Installing in demo-app...${NC}"
cd "$DEMO_DIR"
uv pip install -e . > /dev/null 2>&1

# Start server
echo -e "\n${YELLOW}Starting server on $HOST:$PORT...${NC}"
export PYTHONPATH="$DEMO_DIR"
export YOMAI_ENV=production

uv run uvicorn app.main:app --host "$HOST" --port "$PORT" --log-level warning > "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

# Wait for server
echo "Waiting for server..."
for i in {1..30}; do
    if curl -s "$URL/__yomai__/health" > /dev/null 2>&1; then
        echo -e "${GREEN}Server ready!${NC}"
        break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo -e "${RED}Server process died!${NC}"
        cat "$LOG_FILE"
        exit 1
    fi
    sleep 0.5
done

if [ $i -eq 30 ]; then
    echo -e "${RED}Server failed to start${NC}"
    cat "$LOG_FILE"
    exit 1
fi

echo -e "\n${YELLOW}=== Running Tests ===${NC}\n"

# Test 1: Health
echo -n "Health check... "
RESPONSE=$(curl -s "$URL/__yomai__/health")
test_contains "Health check" "$RESPONSE" "ok"

# Test 2: Metrics
echo -n "Metrics endpoint... "
RESPONSE=$(curl -s "$URL/metrics")
test_contains "Metrics" "$RESPONSE" "requests_total"

# Test 3: OpenAPI schema
echo -n "OpenAPI schema... "
RESPONSE=$(curl -s "$URL/__yomai__/openapi.json")
test_contains "OpenAPI" "$RESPONSE" '"/research"'

# Test 4: Routes list
echo -n "Routes list... "
RESPONSE=$(curl -s "$URL/__yomai__/routes")
test_contains "Routes" "$RESPONSE" '"/research"'
test_contains "Routes" "$RESPONSE" '"/batch-research"'

# Test 5: Session endpoint (GET)
echo -n "Session GET... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$URL/sessions/test-session-get")
test_result "Session GET" "200" "$STATUS"

# Test 6: DELETE requires auth
echo -n "DELETE auth required... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$URL/sessions/test-auth")
test_result "DELETE auth" "401" "$STATUS"

# Test 7: DELETE with auth works
echo -n "DELETE with auth... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
    "$URL/sessions/test-auth" \
    -H "Authorization: Bearer secret")
test_result "DELETE with auth" "200" "$STATUS"

# Test 8: Workflow creates job
echo -n "Async workflow job creation... "
RESPONSE=$(curl -s -X POST "$URL/batch-research" \
    -H "Content-Type: application/json" \
    -d '{"topics":["test"]}')
test_contains "Workflow" "$RESPONSE" "job_id"
JOB_ID=$(echo "$RESPONSE" | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)

# Test 9: Job status
echo -n "Job status lookup... "
if [ -n "$JOB_ID" ]; then
    RESPONSE=$(curl -s "$URL/jobs/$JOB_ID")
    test_contains "Job status" "$RESPONSE" '"id"'
    test_contains "Job status" "$RESPONSE" '"route"'
fi

# Test 10: V2 routes
echo -n "V2 routes... "
RESPONSE=$(curl -s "$URL/__yomai__/openapi.json")
test_contains "V2 research" "$RESPONSE" '"/v2/research"'
test_contains "V2 batch" "$RESPONSE" '"/v2/batch-research"'

# Test 11: CORS headers
echo -n "CORS headers... "
RESPONSE=$(curl -s -I -X OPTIONS "$URL/research")
test_contains "CORS" "$RESPONSE" "access-control-allow-origin"

# Test 12: Invalid request (no message)
echo -n "Validation (missing message)... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "$URL/research" -H "Content-Type: application/json" -d '{}')
test_result "Validation" "400" "$STATUS"

# Test 13: Invalid request (missing topics)
echo -n "Workflow validation... "
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "$URL/batch-research" -H "Content-Type: application/json" -d '{}')
test_result "Workflow validation" "400" "$STATUS"

# Test 14: Job not found
echo -n "Job not found... "
RESPONSE=$(curl -s "$URL/jobs/nonexistent-job-xyz")
test_contains "Job not found" "$RESPONSE" '"error"'

# Summary
echo -e "\n${YELLOW}=== Summary ===${NC}"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"

if [ $FAILED -eq 0 ]; then
    echo -e "\n${GREEN}All production tests passed!${NC}"
else
    echo -e "\n${RED}Some tests failed!${NC}"
fi

exit $FAILED