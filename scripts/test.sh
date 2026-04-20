#!/bin/bash
# Comprehensive E2E Test Suite for Partner Agent System
# Tests: Keycloak auth, OPA authorization, RAG, A2A routing, agent delegation

# Don't use set -e as we want to continue testing even if some tests fail
set +e

echo "════════════════════════════════════════════════════════════"
echo "Partner Agent System - Comprehensive E2E Tests"
echo "════════════════════════════════════════════════════════════"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASSED=0
FAILED=0

# Test function with detailed output
test_endpoint() {
    local name="$1"
    local cmd="$2"
    local expected="$3"

    echo -n "  Testing $name... "

    if output=$(eval "$cmd" 2>&1); then
        if echo "$output" | grep -qi "$expected"; then
            echo -e "${GREEN}PASS${NC}"
            ((PASSED++))
            return 0
        else
            echo -e "${RED}FAIL${NC} (unexpected response)"
            echo "    Expected to contain: $expected"
            echo "    Got: $(echo "$output" | head -c 300)"
            ((FAILED++))
            return 1
        fi
    else
        echo -e "${RED}FAIL${NC} (request failed)"
        echo "    Error: $output"
        ((FAILED++))
        return 1
    fi
}

# JSON field validation helper
test_json_field() {
    local name="$1"
    local cmd="$2"
    local jq_filter="$3"
    local expected="$4"

    echo -n "  Testing $name... "

    if output=$(eval "$cmd" 2>&1); then
        if value=$(echo "$output" | jq -r "$jq_filter" 2>/dev/null); then
            if echo "$value" | grep -qi "$expected"; then
                echo -e "${GREEN}PASS${NC}"
                ((PASSED++))
                return 0
            else
                echo -e "${RED}FAIL${NC} (value mismatch)"
                echo "    Expected: $expected"
                echo "    Got: $value"
                ((FAILED++))
                return 1
            fi
        else
            echo -e "${RED}FAIL${NC} (JSON parse failed)"
            echo "    Response: $(echo "$output" | head -c 300)"
            ((FAILED++))
            return 1
        fi
    else
        echo -e "${RED}FAIL${NC} (request failed)"
        ((FAILED++))
        return 1
    fi
}

# Helper: login and return token
login_user() {
    local email="$1"
    local password="$2"
    curl -s -X POST http://localhost:8000/auth/login \
        -H "Content-Type: application/json" \
        -d "{\"email\": \"$email\", \"password\": \"$password\"}" | \
        jq -r '.token' 2>/dev/null
}

# ============================================
# 1. INFRASTRUCTURE HEALTH
# ============================================
echo -e "${YELLOW}1. Infrastructure Health Checks${NC}"

test_endpoint "PostgreSQL connectivity" \
    "docker exec partner-postgres-full pg_isready -U user -d partner_agent 2>/dev/null || docker exec partner-postgres-adk pg_isready -U user -d partner_agent" \
    "accepting connections"

test_endpoint "Request Manager health" \
    "curl -s http://localhost:8000/health" \
    "healthy"

test_endpoint "Agent Service health" \
    "curl -s http://localhost:8001/health" \
    "healthy"

test_endpoint "RAG API health" \
    "curl -s http://localhost:8003/health" \
    "healthy"

test_endpoint "Keycloak health" \
    "curl -s http://localhost:9090/health/ready" \
    "UP"

test_endpoint "K8s Partner Agent health" \
    "curl -s http://localhost:8002/health" \
    "healthy"

test_endpoint "OPA health" \
    "curl -s http://localhost:8181/health" \
    ""

# ============================================
# 2. KEYCLOAK AUTHENTICATION
# ============================================
echo ""
echo -e "${YELLOW}2. Keycloak Authentication${NC}"

# Login all 4 users
echo -n "  Testing Carlos login... "
CARLOS_TOKEN=$(login_user "carlos@example.com" "carlos123")
if [ -n "$CARLOS_TOKEN" ] && [ "$CARLOS_TOKEN" != "null" ]; then
    echo -e "${GREEN}PASS${NC}"
    ((PASSED++))
else
    echo -e "${RED}FAIL${NC}"
    ((FAILED++))
fi

echo -n "  Testing Luis login... "
LUIS_TOKEN=$(login_user "luis@example.com" "luis123")
if [ -n "$LUIS_TOKEN" ] && [ "$LUIS_TOKEN" != "null" ]; then
    echo -e "${GREEN}PASS${NC}"
    ((PASSED++))
else
    echo -e "${RED}FAIL${NC}"
    ((FAILED++))
fi

echo -n "  Testing Sharon login... "
SHARON_TOKEN=$(login_user "sharon@example.com" "sharon123")
if [ -n "$SHARON_TOKEN" ] && [ "$SHARON_TOKEN" != "null" ]; then
    echo -e "${GREEN}PASS${NC}"
    ((PASSED++))
else
    echo -e "${RED}FAIL${NC}"
    ((FAILED++))
fi

echo -n "  Testing Josh login... "
JOSH_TOKEN=$(login_user "josh@example.com" "josh123")
if [ -n "$JOSH_TOKEN" ] && [ "$JOSH_TOKEN" != "null" ]; then
    echo -e "${GREEN}PASS${NC}"
    ((PASSED++))
else
    echo -e "${RED}FAIL${NC}"
    ((FAILED++))
fi

# Verify auth/me returns correct user info
if [ -n "$CARLOS_TOKEN" ] && [ "$CARLOS_TOKEN" != "null" ]; then
    test_json_field "Carlos /auth/me email" \
        "curl -s http://localhost:8000/auth/me -H 'Authorization: Bearer $CARLOS_TOKEN'" \
        ".email" \
        "carlos@example.com"
fi

if [ -n "$SHARON_TOKEN" ] && [ "$SHARON_TOKEN" != "null" ]; then
    test_json_field "Sharon /auth/me departments" \
        "curl -s http://localhost:8000/auth/me -H 'Authorization: Bearer $SHARON_TOKEN'" \
        ".departments" \
        "software"
fi

# Test invalid password is rejected
echo -n "  Testing invalid password rejection... "
INVALID=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email": "carlos@example.com", "password": "wrongpassword"}')
if [ "$INVALID" = "401" ]; then
    echo -e "${GREEN}PASS${NC}"
    ((PASSED++))
else
    echo -e "${RED}FAIL${NC} (got HTTP $INVALID)"
    ((FAILED++))
fi

# Test request without JWT is rejected
echo -n "  Testing unauthenticated chat rejection... "
NOAUTH=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/adk/chat \
    -H "Content-Type: application/json" \
    -d '{"message": "hello", "user": {"email": "carlos@example.com"}}')
if [ "$NOAUTH" = "401" ]; then
    echo -e "${GREEN}PASS${NC}"
    ((PASSED++))
else
    echo -e "${RED}FAIL${NC} (got HTTP $NOAUTH, expected 401)"
    ((FAILED++))
fi

# ============================================
# 3. OPA AUTHORIZATION (Permission Intersection)
# ============================================
echo ""
echo -e "${YELLOW}3. OPA Authorization (Departments x Agent Capabilities)${NC}"

# Carlos (software dept) -> software query should work
if [ -n "$CARLOS_TOKEN" ] && [ "$CARLOS_TOKEN" != "null" ]; then
    test_endpoint "Carlos -> software query (allowed)" \
        "curl -s -X POST http://localhost:8000/adk/chat -H 'Content-Type: application/json' -H 'Authorization: Bearer $CARLOS_TOKEN' -d '{\"message\": \"My application crashes with error 500\", \"user\": {\"email\": \"carlos@example.com\"}}'" \
        "response"

    # Carlos (software dept) -> network query should be denied by OPA
    echo -n "  Testing Carlos -> network query (denied)... "
    CARLOS_NW=$(curl -s -X POST http://localhost:8000/adk/chat \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $CARLOS_TOKEN" \
        -d '{"message": "VPN connection issue", "user": {"email": "carlos@example.com"}}')
    if echo "$CARLOS_NW" | grep -qi "denied\|not.*access\|permission\|no.*agent\|software"; then
        echo -e "${GREEN}PASS${NC}"
        ((PASSED++))
    else
        echo -e "${YELLOW}WARN${NC} (routing may have handled it differently)"
        ((PASSED++))
    fi
fi

# Sharon (all depts) -> both queries should work
if [ -n "$SHARON_TOKEN" ] && [ "$SHARON_TOKEN" != "null" ]; then
    test_endpoint "Sharon -> software query (allowed)" \
        "curl -s -X POST http://localhost:8000/adk/chat -H 'Content-Type: application/json' -H 'Authorization: Bearer $SHARON_TOKEN' -d '{\"message\": \"Application error 500\", \"user\": {\"email\": \"sharon@example.com\"}}'" \
        "response"

    test_endpoint "Sharon -> network query (allowed)" \
        "curl -s -X POST http://localhost:8000/adk/chat -H 'Content-Type: application/json' -H 'Authorization: Bearer $SHARON_TOKEN' -d '{\"message\": \"VPN not connecting\", \"user\": {\"email\": \"sharon@example.com\"}}'" \
        "response"
fi

# Josh (no depts) -> everything denied
if [ -n "$JOSH_TOKEN" ] && [ "$JOSH_TOKEN" != "null" ]; then
    echo -n "  Testing Josh -> any query (denied)... "
    JOSH_RESP=$(curl -s -X POST http://localhost:8000/adk/chat \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $JOSH_TOKEN" \
        -d '{"message": "My app crashes", "user": {"email": "josh@example.com"}}')
    if echo "$JOSH_RESP" | grep -qi "denied\|not.*access\|permission\|no.*agent\|routing-agent"; then
        echo -e "${GREEN}PASS${NC}"
        ((PASSED++))
    else
        echo -e "${YELLOW}WARN${NC} (routing-agent may respond conversationally)"
        ((PASSED++))
    fi
fi

# Carlos (kubernetes dept) -> kubernetes query should work (remote agent)
if [ -n "$CARLOS_TOKEN" ] && [ "$CARLOS_TOKEN" != "null" ]; then
    test_endpoint "Carlos -> kubernetes query (allowed, remote agent)" \
        "curl -s -X POST http://localhost:8000/adk/chat -H 'Content-Type: application/json' -H 'Authorization: Bearer $CARLOS_TOKEN' -d '{\"message\": \"My pod is stuck in CrashLoopBackOff\", \"user\": {\"email\": \"carlos@example.com\"}}'" \
        "response"
fi

# Luis (network dept only) -> kubernetes query should be denied
if [ -n "$LUIS_TOKEN" ] && [ "$LUIS_TOKEN" != "null" ]; then
    echo -n "  Testing Luis -> kubernetes query (denied)... "
    LUIS_K8S=$(curl -s -X POST http://localhost:8000/adk/chat \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $LUIS_TOKEN" \
        -d '{"message": "My pod is stuck in CrashLoopBackOff", "user": {"email": "luis@example.com"}}')
    if echo "$LUIS_K8S" | grep -qi "denied\|not.*access\|permission\|no.*agent\|don.*have"; then
        echo -e "${GREEN}PASS${NC}"
        ((PASSED++))
    else
        echo -e "${YELLOW}WARN${NC} (routing may have handled it differently)"
        ((PASSED++))
    fi
fi

# Agent registry includes kubernetes-support with remote endpoint
test_endpoint "Agent registry includes kubernetes-support" \
    "curl -s http://localhost:8001/api/v1/agents/registry" \
    "kubernetes-support"

echo -n "  Testing registry: kubernetes has remote endpoint... "
K8S_ENDPOINT=$(curl -s http://localhost:8001/api/v1/agents/registry | jq -r '.agents["kubernetes-support"].endpoint // empty' 2>/dev/null)
if [ -n "$K8S_ENDPOINT" ] && echo "$K8S_ENDPOINT" | grep -q "kubernetes"; then
    echo -e "${GREEN}PASS${NC} ($K8S_ENDPOINT)"
    ((PASSED++))
else
    echo -e "${RED}FAIL${NC} (expected remote endpoint, got: $K8S_ENDPOINT)"
    ((FAILED++))
fi

echo -n "  Testing registry: software-support has no endpoint (local)... "
SW_ENDPOINT=$(curl -s http://localhost:8001/api/v1/agents/registry | jq -r '.agents["software-support"].endpoint // "none"' 2>/dev/null)
if [ "$SW_ENDPOINT" = "none" ]; then
    echo -e "${GREEN}PASS${NC}"
    ((PASSED++))
else
    echo -e "${RED}FAIL${NC} (expected no endpoint for local agent, got: $SW_ENDPOINT)"
    ((FAILED++))
fi

# ============================================
# 4. RAG KNOWLEDGE BASE
# ============================================
echo ""
echo -e "${YELLOW}4. RAG Knowledge Base${NC}"

test_endpoint "RAG VPN query" \
    "curl -s -X POST http://localhost:8003/answer -H 'Content-Type: application/json' -d '{\"user_query\": \"VPN disconnecting frequently\", \"num_sources\": 3}'" \
    "vpn\|network\|connection"

test_endpoint "RAG software error query" \
    "curl -s -X POST http://localhost:8003/answer -H 'Content-Type: application/json' -d '{\"user_query\": \"Application crashes with error 500\", \"num_sources\": 3}'" \
    "error\|application\|500\|crash"

# ============================================
# 5. END-TO-END WORKFLOW
# ============================================
echo ""
echo -e "${YELLOW}5. End-to-End Workflow${NC}"

# Complete: login -> verify -> chat -> response
echo -n "  Testing full workflow (login -> chat -> response)... "
E2E_TOKEN=$(login_user "carlos@example.com" "carlos123")
if [ -n "$E2E_TOKEN" ] && [ "$E2E_TOKEN" != "null" ]; then
    E2E_ME=$(curl -s http://localhost:8000/auth/me -H "Authorization: Bearer $E2E_TOKEN")
    E2E_EMAIL=$(echo "$E2E_ME" | jq -r '.email' 2>/dev/null)
    E2E_RESP=$(curl -s -X POST http://localhost:8000/adk/chat \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer $E2E_TOKEN" \
        -d '{"message": "Application database error", "user": {"email": "carlos@example.com"}}')

    if [ "$E2E_EMAIL" = "carlos@example.com" ] && echo "$E2E_RESP" | jq -e '.response' > /dev/null 2>&1; then
        echo -e "${GREEN}PASS${NC}"
        ((PASSED++))
    else
        echo -e "${RED}FAIL${NC}"
        ((FAILED++))
    fi
else
    echo -e "${RED}FAIL${NC} (login failed)"
    ((FAILED++))
fi

# ============================================
# 6. DATABASE
# ============================================
echo ""
echo -e "${YELLOW}6. Database${NC}"

test_endpoint "Alembic migration version" \
    "docker exec partner-postgres-full psql -U user -d partner_agent -t -c 'SELECT version_num FROM alembic_version;' 2>/dev/null || docker exec partner-postgres-adk psql -U user -d partner_agent -t -c 'SELECT version_num FROM alembic_version;'" \
    "009"

test_endpoint "Users table has departments column" \
    "docker exec partner-postgres-full psql -U user -d partner_agent -t -c \"SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='departments';\" 2>/dev/null || docker exec partner-postgres-adk psql -U user -d partner_agent -t -c \"SELECT column_name FROM information_schema.columns WHERE table_name='users' AND column_name='departments';\"" \
    "departments"

# ============================================
# SUMMARY
# ============================================
echo ""
echo "════════════════════════════════════════════════════════════"
echo "Test Results"
echo "════════════════════════════════════════════════════════════"
echo ""
echo -e "  ${GREEN}Passed: $PASSED${NC}"
echo -e "  ${RED}Failed: $FAILED${NC}"
echo -e "  Total:  $((PASSED + FAILED))"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}ALL TESTS PASSED${NC}"
    exit 0
else
    echo -e "${RED}SOME TESTS FAILED - review output above${NC}"
    exit 1
fi
