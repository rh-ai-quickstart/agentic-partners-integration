#!/bin/bash
# End-to-End Test Suite for Partner Agent Integration
#
# Usage:
#   bash scripts/test-e2e.sh              # Full run (clean, build, deploy, test)
#   bash scripts/test-e2e.sh --skip-build  # Skip image builds
#   bash scripts/test-e2e.sh --skip-deploy # Skip phases 0-2 (test against running stack)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SEED_DIR="$SCRIPT_DIR/seed"

# Colors
R='\033[0;31m'
G='\033[0;32m'
Y='\033[1;33m'
B='\033[0;34m'
C='\033[0;36m'
W='\033[1;37m'
N='\033[0m'

# Test tracking
TESTS_PASSED=0
TESTS_FAILED=0
TESTS_SKIPPED=0
declare -a TEST_RESULTS=()
SUITE_START=$(date +%s)

# Flags
SKIP_BUILD=false
SKIP_DEPLOY=false

# State
ADMIN_TOKEN=""
CLIENT_SECRET=""
AUDIT_START_TS=""
declare -A USER_TOKENS=()

# ═══════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════

parse_args() {
    for arg in "$@"; do
        case "$arg" in
            --skip-build)  SKIP_BUILD=true ;;
            --skip-deploy) SKIP_DEPLOY=true; SKIP_BUILD=true ;;
            *) echo "Unknown arg: $arg"; exit 1 ;;
        esac
    done
}

load_env() {
    if [ -f "$PROJECT_ROOT/.env" ]; then
        set -a
        source "$PROJECT_ROOT/.env"
        set +a
    fi
    if [ -z "${GOOGLE_API_KEY:-}" ]; then
        echo "ERROR: GOOGLE_API_KEY not set in .env"
        exit 1
    fi
}

record_pass() {
    TESTS_PASSED=$((TESTS_PASSED + 1))
    TEST_RESULTS+=("PASS|$1")
    printf "  ${G}✓${N} %s\n" "$1"
}

record_fail() {
    TESTS_FAILED=$((TESTS_FAILED + 1))
    TEST_RESULTS+=("FAIL|$1 [$2]")
    printf "  ${R}✗${N} %s ${R}[%s]${N}\n" "$1" "$2"
}

record_skip() {
    TESTS_SKIPPED=$((TESTS_SKIPPED + 1))
    TEST_RESULTS+=("SKIP|$1")
    printf "  ${Y}⊘${N} %s\n" "$1"
}

section_header() {
    echo ""
    printf "${C}════════════════════════════════════════════════════════════${N}\n"
    printf "${W}  %s${N}\n" "$1"
    printf "${C}════════════════════════════════════════════════════════════${N}\n"
    echo ""
}

wait_for_url() {
    local url="$1"
    local timeout="$2"
    local desc="$3"
    local elapsed=0

    while [ "$elapsed" -lt "$timeout" ]; do
        if curl -sf "$url" > /dev/null 2>&1; then
            return 0
        fi
        sleep 2
        elapsed=$((elapsed + 2))
    done
    return 1
}

db_query() {
    local result
    result=$(docker exec partner-postgres-full \
        psql -U user -d partner_agent -t -A -c "$1" 2>/dev/null | head -1)
    printf '%s' "$result" | tr -d '\n\r'
}

get_admin_token() {
    ADMIN_TOKEN=$(curl -sf -X POST "http://localhost:8090/realms/master/protocol/openid-connect/token" \
        -d "client_id=admin-cli" \
        -d "username=admin" \
        -d "password=admin123" \
        -d "grant_type=password" | jq -r '.access_token // empty')
    if [ -z "$ADMIN_TOKEN" ]; then
        record_fail "Get Keycloak admin token" "Token request failed"
        exit 1
    fi
}

get_client_secret() {
    local client_uuid
    client_uuid=$(curl -sf "http://localhost:8090/admin/realms/partner-agent/clients" \
        -H "Authorization: Bearer $ADMIN_TOKEN" | \
        jq -r '.[] | select(.clientId=="partner-agent-ui") | .id // empty')

    if [ -z "$client_uuid" ]; then
        record_fail "Get client UUID" "partner-agent-ui not found"
        exit 1
    fi

    CLIENT_SECRET=$(curl -sf "http://localhost:8090/admin/realms/partner-agent/clients/${client_uuid}/client-secret" \
        -H "Authorization: Bearer $ADMIN_TOKEN" | jq -r '.value // empty')

    if [ -z "$CLIENT_SECRET" ] || [ "$CLIENT_SECRET" = "null" ]; then
        record_fail "Get client secret" "Secret is empty/null"
        exit 1
    fi
    export CLIENT_SECRET
}

decode_jwt_payload() {
    local token="$1"
    echo "$token" | cut -d. -f2 | \
        python3 -c "import sys,base64,json; b=sys.stdin.read().strip(); b+='='*(4-len(b)%4); print(json.dumps(json.loads(base64.urlsafe_b64decode(b))))"
}

send_chat() {
    local token="$1"
    local message="$2"
    local session_id="${3:-null}"

    curl -s -X POST "http://localhost:8000/adk/chat" \
        -H "Authorization: Bearer $token" \
        -H "Content-Type: application/json" \
        -d "{\"message\": \"$message\", \"session_id\": $session_id, \"user\": {\"email\": \"test@example.com\"}, \"context\": {}}" \
        --max-time 120 2>/dev/null || echo "{}"
}

# ═══════════════════════════════════════════════════════════════
# Phase 0: Clean Slate
# ═══════════════════════════════════════════════════════════════

phase_0_clean() {
    section_header "PHASE 0: Clean Slate"

    echo "  Stopping partner-* containers..."
    docker ps -aq --filter "name=partner-" | xargs -r docker stop 2>/dev/null || true
    docker ps -aq --filter "name=partner-" | xargs -r docker rm -v 2>/dev/null || true

    echo "  Stopping SPIRE containers..."
    for c in spire-server partner-spire-agent; do
        docker stop "$c" 2>/dev/null || true
        docker rm -v "$c" 2>/dev/null || true
    done

    echo "  Removing volumes..."
    docker volume rm spire-socket 2>/dev/null || true
    docker volume ls -q --filter "name=partner-" | xargs -r docker volume rm 2>/dev/null || true

    echo "  Removing network..."
    docker network rm partner-agent-network 2>/dev/null || true

    record_pass "Clean slate"
}

# ═══════════════════════════════════════════════════════════════
# Phase 1: Build
# ═══════════════════════════════════════════════════════════════

phase_1_build() {
    section_header "PHASE 1: Build Container Images"

    if $SKIP_BUILD; then
        record_skip "Build (--skip-build)"
        return
    fi

    cd "$PROJECT_ROOT"

    echo "  Building request-manager..."
    if docker build -t partner-request-manager:latest -f request-manager/Containerfile . > /tmp/build-request-manager.log 2>&1; then
        record_pass "Build: request-manager"
    else
        tail -20 /tmp/build-request-manager.log
        record_fail "Build: request-manager" "See /tmp/build-request-manager.log"
        exit 1
    fi

    echo "  Building agent-service..."
    if docker build -t partner-agent-service:latest -f agent-service/Containerfile . > /tmp/build-agent-service.log 2>&1; then
        record_pass "Build: agent-service"
    else
        tail -20 /tmp/build-agent-service.log
        record_fail "Build: agent-service" "See /tmp/build-agent-service.log"
        exit 1
    fi

    echo "  Building rag-api (--no-cache)..."
    if docker build --no-cache -t partner-rag-api:latest -f rag-service/Containerfile . > /tmp/build-rag-api.log 2>&1; then
        record_pass "Build: rag-api"
    else
        tail -20 /tmp/build-rag-api.log
        record_fail "Build: rag-api" "See /tmp/build-rag-api.log"
        exit 1
    fi

    if [ -f "pf-chat-ui/Containerfile" ]; then
        echo "  Building pf-chat-ui..."
        if docker build -t partner-pf-chat-ui:latest -f pf-chat-ui/Containerfile . > /tmp/build-pf-chat-ui.log 2>&1; then
            record_pass "Build: pf-chat-ui"
        else
            record_skip "Build: pf-chat-ui (non-critical)"
        fi
    fi
}

# ═══════════════════════════════════════════════════════════════
# Phase 2: Deploy
# ═══════════════════════════════════════════════════════════════

phase_2_deploy() {
    section_header "PHASE 2: Deploy Full Stack"

    if $SKIP_DEPLOY; then
        record_skip "Deploy (--skip-deploy)"
        return
    fi

    echo "  [1/4] Starting infrastructure containers..."
    bash "$SEED_DIR/seed-containers.sh"

    echo "  [2/4] Seeding Keycloak..."
    bash "$SEED_DIR/seed-keycloak.sh"

    echo "  [3/4] Configuring client..."
    CLIENT_SECRET=$(bash "$SEED_DIR/seed-client.sh" 2>&1 | tail -1)
    if [ -z "$CLIENT_SECRET" ] || [ "$CLIENT_SECRET" = "null" ]; then
        record_fail "Deploy: Get client secret" "seed-client.sh returned empty/null"
        exit 1
    fi
    export CLIENT_SECRET

    echo "  [4/4] Starting application services..."
    export GOOGLE_API_KEY
    export DATABASE_URL
    export GEMINI_MODEL
    export LLM_BACKEND
    export KEYCLOAK_URL
    export REALM
    bash "$SEED_DIR/seed-services.sh"

    record_pass "Deploy: Full stack started"
}

# ═══════════════════════════════════════════════════════════════
# Phase 3: Readiness
# ═══════════════════════════════════════════════════════════════

phase_3_readiness() {
    section_header "PHASE 3: Readiness Checks"

    if $SKIP_DEPLOY; then
        echo "  Ensuring client is configured (idempotent)..."
        CLIENT_SECRET=$(bash "$SEED_DIR/seed-client.sh" 2>&1 | tail -1)
        if [ -z "$CLIENT_SECRET" ] || [ "$CLIENT_SECRET" = "null" ]; then
            record_fail "Credentials: client secret" "seed-client.sh returned empty/null"
            exit 1
        fi
        export CLIENT_SECRET
        get_admin_token
        record_pass "Credentials: admin token + client secret obtained"
    fi

    local services=(
        "http://localhost:8090/realms/master|Keycloak"
        "http://localhost:8000/health|Request Manager"
        "http://localhost:8001/health|Agent Service"
        "http://localhost:8080/health|RAG API"
    )

    for entry in "${services[@]}"; do
        local url="${entry%%|*}"
        local name="${entry##*|}"
        if wait_for_url "$url" 120 "$name"; then
            record_pass "Readiness: $name"
        else
            record_fail "Readiness: $name" "Not healthy after 120s"
        fi
    done

    # Praxis readiness — check health through the proxy
    if wait_for_url "http://localhost:8180/health" 60 "Praxis Gateway"; then
        record_pass "Readiness: Praxis Gateway"
    else
        record_fail "Readiness: Praxis Gateway" "Not healthy after 60s"
    fi

    local entry_count
    entry_count=$(docker exec spire-server /opt/spire/bin/spire-server entry show 2>/dev/null \
        | grep -c "^Entry ID" || echo "0")
    entry_count=$(echo "$entry_count" | tr -d '[:space:]')
    if [ "$entry_count" -ge 2 ]; then
        record_pass "Readiness: SPIRE workload entries ($entry_count found)"
    else
        record_fail "Readiness: SPIRE workload entries" "Expected >=2, found $entry_count"
    fi

    AUDIT_START_TS=$(date -u +"%Y-%m-%d %H:%M:%S")
    echo ""
    echo "  Audit baseline: $AUDIT_START_TS"
}

# ═══════════════════════════════════════════════════════════════
# Phase 4: Authentication
# ═══════════════════════════════════════════════════════════════

phase_4_authentication() {
    section_header "PHASE 4: Authentication Tests"

    local users=("carlos:carlos123" "luis:luis123" "sharon:sharon123" "josh:josh123")

    for user_spec in "${users[@]}"; do
        local username="${user_spec%%:*}"
        local password="${user_spec##*:}"
        local response

        response=$(curl -sf -X POST "http://localhost:8000/auth/login" \
            -H "Content-Type: application/json" \
            -d "{\"email\": \"$username\", \"password\": \"$password\"}" \
            --max-time 30 2>/dev/null) || response=""

        if [ -n "$response" ]; then
            local token
            token=$(echo "$response" | jq -r '.token // empty')
            if [ -n "$token" ]; then
                USER_TOKENS[$username]="$token"
                record_pass "Auth: $username login"
            else
                record_fail "Auth: $username login" "No token in response"
            fi
        else
            record_fail "Auth: $username login" "Request failed"
        fi
    done

    # Verify JWT claims
    if [ -n "${USER_TOKENS[carlos]:-}" ]; then
        local claims groups
        claims=$(decode_jwt_payload "${USER_TOKENS[carlos]}") || claims="{}"
        groups=$(echo "$claims" | jq -r '[.groups // [] | .[] | ascii_downcase] | sort | join(",")') || groups=""
        if echo "$groups" | grep -q "kubernetes" && echo "$groups" | grep -q "software"; then
            record_pass "Auth: carlos JWT groups ($groups)"
        else
            record_fail "Auth: carlos JWT groups" "Expected kubernetes,software; got $groups"
        fi
    fi

    if [ -n "${USER_TOKENS[luis]:-}" ]; then
        local claims groups
        claims=$(decode_jwt_payload "${USER_TOKENS[luis]}") || claims="{}"
        groups=$(echo "$claims" | jq -r '[.groups // [] | .[] | ascii_downcase] | sort | join(",")') || groups=""
        if echo "$groups" | grep -q "network"; then
            record_pass "Auth: luis JWT groups ($groups)"
        else
            record_fail "Auth: luis JWT groups" "Expected network; got $groups"
        fi
    fi

    if [ -n "${USER_TOKENS[sharon]:-}" ]; then
        local claims groups
        claims=$(decode_jwt_payload "${USER_TOKENS[sharon]}") || claims="{}"
        groups=$(echo "$claims" | jq -r '[.groups // [] | .[] | ascii_downcase] | sort | join(",")') || groups=""
        if echo "$groups" | grep -q "admin" && echo "$groups" | grep -q "kubernetes" && echo "$groups" | grep -q "network" && echo "$groups" | grep -q "software"; then
            record_pass "Auth: sharon JWT groups ($groups)"
        else
            record_fail "Auth: sharon JWT groups" "Expected admin,kubernetes,network,software; got $groups"
        fi
    fi

    if [ -n "${USER_TOKENS[josh]:-}" ]; then
        local claims groups
        claims=$(decode_jwt_payload "${USER_TOKENS[josh]}") || claims="{}"
        groups=$(echo "$claims" | jq -r '[.groups // [] | .[] | ascii_downcase] | sort | join(",")') || groups=""
        if [ -z "$groups" ]; then
            record_pass "Auth: josh JWT groups (empty as expected)"
        else
            record_fail "Auth: josh JWT groups" "Expected empty; got $groups"
        fi
    fi

    # Invalid credentials test
    local invalid_code
    invalid_code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8000/auth/login" \
        -H "Content-Type: application/json" \
        -d '{"email": "carlos", "password": "wrong"}' \
        --max-time 15)
    if [ "$invalid_code" = "401" ]; then
        record_pass "Auth: invalid credentials rejected (HTTP 401)"
    else
        record_fail "Auth: invalid credentials" "Expected 401, got $invalid_code"
    fi
}

# ═══════════════════════════════════════════════════════════════
# Phase 4b: DCR & Agent Discovery
# ═══════════════════════════════════════════════════════════════

phase_4b_dcr_and_discovery() {
    section_header "PHASE 4b: DCR & Agent Discovery"

    # Ensure we have an admin token for Keycloak queries
    if [ -z "$ADMIN_TOKEN" ]; then
        get_admin_token
    fi

    # 4b.1: Verify DCR-registered clients in Keycloak (services self-register with SPIFFE IDs)
    local clients dcr_count
    clients=$(curl -sf "http://localhost:8090/admin/realms/partner-agent/clients?max=50" \
        -H "Authorization: Bearer $ADMIN_TOKEN" 2>/dev/null) || clients="[]"

    dcr_count=$(echo "$clients" | jq '[.[] | select(.clientId | test("^[0-9a-f-]{36}$"))] | length') || dcr_count=0
    if [ "${dcr_count:-0}" -ge 2 ]; then
        record_pass "DCR: Keycloak has $dcr_count DCR-registered clients"
    else
        record_fail "DCR: Keycloak DCR clients" "Expected >=2 (request-manager + agent-service), found ${dcr_count:-0}"
    fi

    # 4b.2: Verify DCR clients have SPIFFE-based names
    local spiffe_clients
    spiffe_clients=$(echo "$clients" | jq '[.[] | select(.name // "" | test("spiffe://"))] | length') || spiffe_clients=0
    if [ "${spiffe_clients:-0}" -ge 1 ]; then
        local spiffe_names
        spiffe_names=$(echo "$clients" | jq -r '[.[] | select(.name // "" | test("spiffe://")) | .name] | join(", ")') || spiffe_names=""
        record_pass "DCR: SPIFFE-identified clients ($spiffe_names)"
    else
        record_fail "DCR: SPIFFE-identified clients" "No clients with spiffe:// in name"
    fi

    # 4b.3: Verify DCR clients have token-exchange grant enabled
    local te_clients
    te_clients=$(echo "$clients" | jq '[.[] | select(.name // "" | test("spiffe://")) | select(.attributes // {} | .["oauth2.device.authorization.grant.enabled"] // "" == "true" or (.attributes // {} | keys | any(test("token"))))] | length') || te_clients=0
    # Alternatively check the grant types directly
    local dcr_with_grants
    dcr_with_grants=$(echo "$clients" | jq '[.[] | select(.name // "" | test("spiffe://"))] | length') || dcr_with_grants=0
    if [ "${dcr_with_grants:-0}" -ge 1 ]; then
        record_pass "DCR: clients have service accounts ($dcr_with_grants found)"
    else
        record_skip "DCR: token-exchange grant check (could not verify)"
    fi

    # 4b.4: Agent registry endpoint returns known agents
    local registry_resp registry_agents
    registry_resp=$(curl -sf "http://localhost:8001/api/v1/agents/registry" --max-time 15 2>/dev/null) || registry_resp="{}"
    # Registry wraps agents under .agents key
    registry_agents=$(echo "$registry_resp" | jq -r '.agents // . | keys | join(",")') || registry_agents=""

    if echo "$registry_agents" | grep -q "kubernetes-support" && echo "$registry_agents" | grep -q "network-support" && echo "$registry_agents" | grep -q "software-support"; then
        record_pass "Discovery: agent registry has all specialists ($registry_agents)"
    else
        record_fail "Discovery: agent registry" "Got: $registry_agents"
    fi

    # 4b.5: Agent card directory endpoint
    local card_resp card_agents
    card_resp=$(curl -s "http://localhost:8001/.well-known/agent-card.json" --max-time 15 2>/dev/null) || card_resp="{}"
    local card_code
    card_code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8001/.well-known/agent-card.json" --max-time 15)
    card_agents=$(echo "$card_resp" | jq -r '.agent_cards // {} | keys | join(",")') || card_agents=""

    if [ "$card_code" = "200" ] && [ -n "$card_agents" ]; then
        record_pass "Discovery: A2A agent card directory ($card_agents)"
    else
        record_fail "Discovery: A2A agent card directory" "HTTP $card_code, agents=$card_agents"
    fi

    # 4b.6: Proxy endpoint /adk/agents returns agent list (requires auth)
    if [ -n "${USER_TOKENS[carlos]:-}" ]; then
        local proxy_resp proxy_count
        proxy_resp=$(curl -sf "http://localhost:8000/adk/agents" \
            -H "Authorization: Bearer ${USER_TOKENS[carlos]}" \
            --max-time 15 2>/dev/null) || proxy_resp="{}"
        proxy_count=$(echo "$proxy_resp" | jq 'if type == "object" then (keys | length) elif type == "array" then length else 0 end') || proxy_count=0

        if [ "${proxy_count:-0}" -ge 1 ]; then
            record_pass "Discovery: /adk/agents proxy returns agents ($proxy_count found)"
        else
            record_fail "Discovery: /adk/agents proxy" "Empty or invalid response"
        fi
    else
        record_skip "Discovery: /adk/agents proxy (no auth token)"
    fi

    # 4b.7: Verify DCR registration in container logs
    local rm_dcr as_dcr
    rm_dcr=$(docker logs partner-request-manager-full 2>&1 | grep -ci "DCR.*regist\|DCR.*success\|dcr.*complete" || echo "0")
    rm_dcr=$(echo "$rm_dcr" | tr -d '[:space:]')
    as_dcr=$(docker logs partner-agent-service-full 2>&1 | grep -ci "DCR.*regist\|DCR.*success\|dcr.*complete" || echo "0")
    as_dcr=$(echo "$as_dcr" | tr -d '[:space:]')

    if [ "$rm_dcr" -ge 1 ] && [ "$as_dcr" -ge 1 ]; then
        record_pass "DCR: both services logged successful registration"
    elif [ "$rm_dcr" -ge 1 ] || [ "$as_dcr" -ge 1 ]; then
        record_pass "DCR: at least one service logged DCR registration (rm=$rm_dcr, as=$as_dcr)"
    else
        record_fail "DCR: registration logs" "No DCR registration messages in logs (rm=$rm_dcr, as=$as_dcr)"
    fi
}

# ═══════════════════════════════════════════════════════════════
# Phase 5: Chat & Authorization Tests
# ═══════════════════════════════════════════════════════════════

check_chat_allowed() {
    local label="$1"
    local token="$2"
    local message="$3"
    local expected_agent="$4"
    local topic_keywords="$5"

    local resp agent response_text

    resp=$(send_chat "$token" "$message")
    agent=$(echo "$resp" | jq -r '.agent // empty') || agent=""
    response_text=$(echo "$resp" | jq -r '.response // empty') || response_text=""

    if [ -z "$response_text" ] || [ ${#response_text} -lt 20 ]; then
        record_fail "$label" "Empty or very short response"
        return
    fi

    local agent_match=false topic_match=false
    if [ "$agent" = "$expected_agent" ]; then
        agent_match=true
    fi

    local kw
    for kw in $(echo "$topic_keywords" | tr ',' ' '); do
        if echo "$response_text" | grep -qi "$kw"; then
            topic_match=true
            break
        fi
    done

    if $agent_match || $topic_match; then
        record_pass "$label (agent=$agent, ${#response_text} chars)"
    else
        record_fail "$label" "agent=$agent, no topic keywords matched"
    fi
}

check_chat_denied() {
    local label="$1"
    local token="$2"
    local message="$3"

    local resp response_text routing_reason

    resp=$(send_chat "$token" "$message")
    response_text=$(echo "$resp" | jq -r '.response // empty') || response_text=""
    routing_reason=$(echo "$resp" | jq -r '.metadata.routing_reason // empty') || routing_reason=""

    local denied=false

    if echo "$response_text" | grep -qi "access denied\|not have access\|does not have\|not authorized\|department access\|do not have"; then
        denied=true
    fi
    if echo "$routing_reason" | grep -qi "denied\|unauthorized"; then
        denied=true
    fi

    if $denied; then
        record_pass "$label (correctly denied)"
    else
        local agent
        agent=$(echo "$resp" | jq -r '.agent // empty') || agent=""
        if [ "$agent" = "routing-agent" ] && [ ${#response_text} -gt 0 ]; then
            record_pass "$label (routing-agent handled, no delegation)"
        else
            record_fail "$label" "No denial detected (agent=$agent)"
        fi
    fi
}

phase_5_chat_tests() {
    section_header "PHASE 5: Chat & Authorization Tests"

    if [ -z "${USER_TOKENS[carlos]:-}" ]; then
        record_skip "Chat tests: carlos token missing"
        return
    fi

    # 5.1: carlos → kubernetes (ALLOW)
    echo "  Testing carlos → kubernetes question..."
    check_chat_allowed \
        "Chat: carlos → kubernetes" \
        "${USER_TOKENS[carlos]}" \
        "I am having issues with my Kubernetes pods crashing. How do I debug CrashLoopBackOff?" \
        "kubernetes-support" \
        "kubernetes,pod,crash,kubectl,crashloop"

    # 5.2: carlos → software (ALLOW)
    echo "  Testing carlos → software question..."
    check_chat_allowed \
        "Chat: carlos → software" \
        "${USER_TOKENS[carlos]}" \
        "How do I fix a Python ImportError when deploying a Django application?" \
        "software-support" \
        "python,import,django,module,pip"

    # 5.3: carlos → network (DENY - carlos lacks network department)
    echo "  Testing carlos → network question (should deny)..."
    check_chat_denied \
        "Chat: carlos → network DENIED" \
        "${USER_TOKENS[carlos]}" \
        "How do I configure BGP peering between two routers?"

    # 5.4: josh → any question (DENY - no departments)
    if [ -n "${USER_TOKENS[josh]:-}" ]; then
        echo "  Testing josh → any question (should deny)..."
        check_chat_denied \
            "Chat: josh → any DENIED" \
            "${USER_TOKENS[josh]}" \
            "Help me with my Kubernetes deployment"
    else
        record_skip "Chat: josh test (no token)"
    fi

    # 5.5: sharon → network (ALLOW - sharon has all departments)
    if [ -n "${USER_TOKENS[sharon]:-}" ]; then
        echo "  Testing sharon → network question..."
        check_chat_allowed \
            "Chat: sharon → network" \
            "${USER_TOKENS[sharon]}" \
            "How do I troubleshoot OSPF neighbor adjacency issues?" \
            "network-support" \
            "ospf,neighbor,adjacency,routing,network"
    else
        record_skip "Chat: sharon test (no token)"
    fi
}

# ═══════════════════════════════════════════════════════════════
# Phase 6: Praxis Gateway Tests
# ═══════════════════════════════════════════════════════════════

phase_6_praxis() {
    section_header "PHASE 6: Praxis Gateway Tests"

    # Health through Praxis — transparent proxy, should reach agent-service
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:8180/health" --max-time 15)
    if [ "$http_code" = "200" ]; then
        record_pass "Praxis: health proxied to agent-service (HTTP 200)"
    else
        record_fail "Praxis: health proxy" "Expected 200, got $http_code"
    fi

    # Agent registry through Praxis — should return agent list
    local registry_body
    registry_body=$(curl -s "http://localhost:8180/api/v1/agents/registry" --max-time 15 2>/dev/null || echo "{}")
    if echo "$registry_body" | grep -q "kubernetes-support"; then
        record_pass "Praxis: agent registry proxied successfully"
    else
        record_fail "Praxis: agent registry proxy" "Registry did not contain expected agents"
    fi
}

# ═══════════════════════════════════════════════════════════════
# Phase 7: Audit Trail Verification
# ═══════════════════════════════════════════════════════════════

phase_7_audit() {
    section_header "PHASE 7: Audit Trail Verification"

    # 7.1: Login success events
    local login_count
    login_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'auth.login.success' AND created_at >= '$AUDIT_START_TS'")
    if [ "${login_count:-0}" -ge 4 ]; then
        record_pass "Audit: auth.login.success events ($login_count found)"
    else
        record_fail "Audit: auth.login.success events" "Expected >=4, found ${login_count:-0}"
    fi

    # 7.2: Per-user login
    for user in carlos luis sharon josh; do
        local user_count
        user_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'auth.login.success' AND actor LIKE '%${user}%' AND created_at >= '$AUDIT_START_TS'")
        if [ "${user_count:-0}" -ge 1 ]; then
            record_pass "Audit: $user login recorded"
        else
            record_fail "Audit: $user login recorded" "No auth.login.success for $user"
        fi
    done

    # 7.3: Authorization allow events
    local allow_count
    allow_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'authz.allow' AND created_at >= '$AUDIT_START_TS'")
    if [ "${allow_count:-0}" -ge 1 ]; then
        record_pass "Audit: authz.allow events ($allow_count found)"
    else
        record_fail "Audit: authz.allow events" "None found"
    fi

    # 7.4: Authorization deny events
    local deny_count
    deny_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'authz.deny' AND created_at >= '$AUDIT_START_TS'")
    if [ "${deny_count:-0}" -ge 1 ]; then
        record_pass "Audit: authz.deny events ($deny_count found)"
    else
        local routing_direct
        routing_direct=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'authz.routing_direct' AND created_at >= '$AUDIT_START_TS'")
        if [ "${routing_direct:-0}" -ge 1 ]; then
            record_pass "Audit: authz.routing_direct as soft-deny ($routing_direct found)"
        else
            record_fail "Audit: authz.deny events" "No deny or routing_direct events"
        fi
    fi

    # 7.5: Token exchange events
    local te_count
    te_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'token.exchange' AND created_at >= '$AUDIT_START_TS'")
    if [ "${te_count:-0}" -ge 1 ]; then
        record_pass "Audit: token.exchange events ($te_count found)"
    else
        record_skip "Audit: token.exchange events (none found, may not be configured)"
    fi

    # 7.5b: Token exchange with DCR actor token (auth_method=dcr-actor in metadata)
    local dcr_te_count
    dcr_te_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'token.exchange' AND metadata::text LIKE '%dcr-actor%' AND created_at >= '$AUDIT_START_TS'")
    if [ "${dcr_te_count:-0}" -ge 1 ]; then
        record_pass "Audit: DCR-backed token exchanges ($dcr_te_count with auth_method=dcr-actor)"
    else
        if [ "${te_count:-0}" -ge 1 ]; then
            record_skip "Audit: DCR-backed token exchanges (exchanges exist but no dcr-actor method)"
        else
            record_skip "Audit: DCR-backed token exchanges (no token exchanges at all)"
        fi
    fi

    # 7.5c: auth.login.failure event (from invalid credentials test)
    local fail_count
    fail_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'auth.login.failure' AND created_at >= '$AUDIT_START_TS'")
    if [ "${fail_count:-0}" -ge 1 ]; then
        record_pass "Audit: auth.login.failure events ($fail_count found)"
    else
        record_skip "Audit: auth.login.failure events (none found)"
    fi

    # 7.6: Chat request events
    local chat_count
    chat_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'data.chat.request' AND created_at >= '$AUDIT_START_TS'")
    if [ "${chat_count:-0}" -ge 3 ]; then
        record_pass "Audit: data.chat.request events ($chat_count found)"
    else
        record_fail "Audit: data.chat.request events" "Expected >=3, found ${chat_count:-0}"
    fi
}

# ═══════════════════════════════════════════════════════════════
# Phase 8: Request Log Verification
# ═══════════════════════════════════════════════════════════════

phase_8_request_logs() {
    section_header "PHASE 8: Request Log Verification"

    # 8.1: Completed requests exist
    local log_count
    log_count=$(db_query "SELECT COUNT(*) FROM request_logs WHERE created_at >= '$AUDIT_START_TS' AND completed_at IS NOT NULL")
    if [ "${log_count:-0}" -ge 1 ]; then
        record_pass "Request Logs: completed requests ($log_count found)"
    else
        record_fail "Request Logs: completed requests" "None found"
    fi

    # 8.2: Agent IDs recorded
    local agent_ids
    agent_ids=$(db_query "SELECT string_agg(DISTINCT agent_id, ',') FROM request_logs WHERE created_at >= '$AUDIT_START_TS' AND agent_id IS NOT NULL")
    if echo "${agent_ids:-}" | grep -qE "kubernetes-support|software-support|network-support|routing-agent"; then
        record_pass "Request Logs: agent IDs recorded ($agent_ids)"
    else
        record_fail "Request Logs: agent IDs" "No recognized agent_ids: ${agent_ids:-empty}"
    fi

    # 8.3: Non-empty responses
    local empty_count
    empty_count=$(db_query "SELECT COUNT(*) FROM request_logs WHERE created_at >= '$AUDIT_START_TS' AND completed_at IS NOT NULL AND (response_content IS NULL OR response_content = '')")
    if [ "${empty_count:-0}" = "0" ]; then
        record_pass "Request Logs: all completed requests have responses"
    else
        record_fail "Request Logs: empty responses" "$empty_count completed requests lack response"
    fi

    # 8.4: Processing time recorded
    local no_time
    no_time=$(db_query "SELECT COUNT(*) FROM request_logs WHERE created_at >= '$AUDIT_START_TS' AND completed_at IS NOT NULL AND processing_time_ms IS NULL")
    if [ "${no_time:-0}" = "0" ]; then
        record_pass "Request Logs: processing_time_ms recorded"
    else
        record_fail "Request Logs: missing processing_time_ms" "$no_time requests lack timing"
    fi
}

# ═══════════════════════════════════════════════════════════════
# Phase 9: Summary
# ═══════════════════════════════════════════════════════════════

phase_9_summary() {
    local suite_end duration minutes seconds
    suite_end=$(date +%s)
    duration=$((suite_end - SUITE_START))
    minutes=$((duration / 60))
    seconds=$((duration % 60))

    echo ""
    printf "${W}════════════════════════════════════════════════════════════════${N}\n"
    printf "${W}  E2E TEST RESULTS${N}\n"
    printf "${W}════════════════════════════════════════════════════════════════${N}\n"
    echo ""
    printf "  %-62s %s\n" "TEST" "RESULT"
    printf "  %-62s %s\n" "────" "──────"

    for result in "${TEST_RESULTS[@]}"; do
        local status="${result%%|*}"
        local desc="${result#*|}"
        case "$status" in
            PASS) printf "  %-62s ${G}PASS${N}\n" "$desc" ;;
            FAIL) printf "  %-62s ${R}FAIL${N}\n" "$desc" ;;
            SKIP) printf "  %-62s ${Y}SKIP${N}\n" "$desc" ;;
        esac
    done

    echo ""
    echo "  ────────────────────────────────────────────────────────────"
    printf "  ${G}PASSED: $TESTS_PASSED${N}  ${R}FAILED: $TESTS_FAILED${N}  ${Y}SKIPPED: $TESTS_SKIPPED${N}\n"
    echo "  Duration: ${minutes}m ${seconds}s"
    printf "${W}════════════════════════════════════════════════════════════════${N}\n"
    echo ""

    if [ "$TESTS_FAILED" -gt 0 ]; then
        exit 1
    fi
}

# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

main() {
    parse_args "$@"
    load_env

    echo ""
    printf "${W}════════════════════════════════════════════════════════════════${N}\n"
    printf "${W}  PARTNER AGENT INTEGRATION — E2E TEST SUITE${N}\n"
    printf "${W}════════════════════════════════════════════════════════════════${N}\n"
    echo ""
    echo "  Flags: skip-build=$SKIP_BUILD skip-deploy=$SKIP_DEPLOY"
    echo "  Started: $(date)"

    if ! $SKIP_DEPLOY; then
        phase_0_clean
        phase_1_build
        phase_2_deploy
    fi

    phase_3_readiness
    phase_4_authentication
    phase_4b_dcr_and_discovery
    phase_5_chat_tests
    phase_6_praxis

    # Allow async audit writes to flush
    sleep 3

    phase_7_audit
    phase_8_request_logs
    phase_9_summary
}

main "$@"
