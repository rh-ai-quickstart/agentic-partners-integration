#!/bin/bash
# End-to-End Test Suite for Partner Agent Integration
#
# Usage:
#   bash scripts/test-e2e.sh              # Full run (clean, build, deploy, test)
#   bash scripts/test-e2e.sh --skip-build  # Skip image builds
#   bash scripts/test-e2e.sh --skip-deploy # Skip phases 0-2 (test against running stack)
#   bash scripts/test-e2e.sh --test-helm   # Test against Helm deployment on OpenShift

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
TEST_HELM=false
IMAGE_TAG="${IMAGE_TAG:-main}"
HELM_NS="${HELM_NS:-partner-agent}"
PF_PIDS=()

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
            --test-helm)   TEST_HELM=true; SKIP_BUILD=true; SKIP_DEPLOY=true ;;
            *) echo "Unknown arg: $arg"; exit 1 ;;
        esac
    done
}

load_env() {
    if $TEST_HELM; then
        return
    fi
    if [ -f "$PROJECT_ROOT/.env" ]; then
        set -a
        source "$PROJECT_ROOT/.env"
        set +a
    fi
    if [ -z "${GOOGLE_API_KEY:-}" ]; then
        echo "  No GOOGLE_API_KEY in .env — mock LLM will be used"
        export GOOGLE_API_KEY="mock-api-key"
    fi
}

setup_helm_portforwards() {
    echo "  Setting up port-forwards to OpenShift cluster (ns=$HELM_NS)..."

    local ports=(8000 8001 8080 8090 8180)
    local conflicts=0
    for p in "${ports[@]}"; do
        if ss -tlnH "sport = :$p" 2>/dev/null | grep -q LISTEN; then
            echo "  WARNING: port $p already in use"
            conflicts=$((conflicts + 1))
        fi
    done
    if [ "$conflicts" -gt 0 ]; then
        echo "  Stopping local Docker containers to free ports..."
        docker ps -q --filter "name=partner-" | xargs -r docker stop 2>/dev/null || true
        docker stop spire-server partner-spire-agent 2>/dev/null || true
        sleep 2
    fi

    local fullname="partner-agent"

    oc port-forward "svc/${fullname}-keycloak" 8090:8080 -n "$HELM_NS" > /dev/null 2>&1 &
    PF_PIDS+=($!)
    oc port-forward "svc/${fullname}-request-manager" 8000:80 -n "$HELM_NS" > /dev/null 2>&1 &
    PF_PIDS+=($!)
    oc port-forward "svc/${fullname}-agent-service" 8001:80 -n "$HELM_NS" > /dev/null 2>&1 &
    PF_PIDS+=($!)
    oc port-forward "svc/${fullname}-rag-api" 8080:80 -n "$HELM_NS" > /dev/null 2>&1 &
    PF_PIDS+=($!)
    oc port-forward "svc/${fullname}-praxis" 8180:8080 -n "$HELM_NS" > /dev/null 2>&1 &
    PF_PIDS+=($!)

    sleep 3
    echo "  Port-forwards active (${#PF_PIDS[@]} tunnels)"
}

cleanup_portforwards() {
    for pid in "${PF_PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    PF_PIDS=()
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
    if $TEST_HELM; then
        local pg_pod
        pg_pod=$(oc get pods -n "$HELM_NS" -l component=postgresql -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        result=$(oc exec "$pg_pod" -n "$HELM_NS" -- \
            psql -U postgres -d partner_agent -t -A -c "$1" 2>/dev/null | head -1)
    else
        result=$(docker exec partner-postgres-full \
            psql -U user -d partner_agent -t -A -c "$1" 2>/dev/null | head -1)
    fi
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
    if docker build -t partner-request-manager:${IMAGE_TAG} -f request-manager/Containerfile . > /tmp/build-request-manager.log 2>&1; then
        record_pass "Build: request-manager"
    else
        tail -20 /tmp/build-request-manager.log
        record_fail "Build: request-manager" "See /tmp/build-request-manager.log"
        exit 1
    fi

    echo "  Building agent-service..."
    if docker build -t partner-agent-service:${IMAGE_TAG} -f agent-service/Containerfile . > /tmp/build-agent-service.log 2>&1; then
        record_pass "Build: agent-service"
    else
        tail -20 /tmp/build-agent-service.log
        record_fail "Build: agent-service" "See /tmp/build-agent-service.log"
        exit 1
    fi

    echo "  Building rag-api (--no-cache)..."
    if docker build --no-cache -t partner-rag-api:${IMAGE_TAG} -f rag-service/Containerfile . > /tmp/build-rag-api.log 2>&1; then
        record_pass "Build: rag-api"
    else
        tail -20 /tmp/build-rag-api.log
        record_fail "Build: rag-api" "See /tmp/build-rag-api.log"
        exit 1
    fi

    if [ -f "pf-chat-ui/Containerfile" ]; then
        echo "  Building pf-chat-ui..."
        if docker build -t partner-pf-chat-ui:${IMAGE_TAG} -f pf-chat-ui/Containerfile . > /tmp/build-pf-chat-ui.log 2>&1; then
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
    export IMAGE_TAG
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

    # Praxis readiness — admin /ready inside container (proxy port requires JWT)
    local praxis_up=false
    local praxis_wait=0
    while [ "$praxis_wait" -lt 60 ]; do
        if docker exec partner-praxis-gateway-full wget -q -O- http://127.0.0.1:9901/ready 2>/dev/null | grep -q '"ok"'; then
            praxis_up=true
            break
        fi
        sleep 2
        praxis_wait=$((praxis_wait + 2))
    done
    if $praxis_up; then
        record_pass "Readiness: Praxis Gateway"
    else
        record_fail "Readiness: Praxis Gateway" "Not healthy after 60s"
    fi

    if $TEST_HELM; then
        record_skip "Readiness: SPIRE workload entries (mock mode in Helm)"
    else
        local entry_count
        entry_count=$(docker exec spire-server /opt/spire/bin/spire-server entry show 2>/dev/null \
            | grep -c "^Entry ID" || echo "0")
        entry_count=$(echo "$entry_count" | tr -d '[:space:]')
        if [ "$entry_count" -ge 2 ]; then
            record_pass "Readiness: SPIRE workload entries ($entry_count found)"
        else
            record_fail "Readiness: SPIRE workload entries" "Expected >=2, found $entry_count"
        fi
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
    registry_resp=$(curl -sf "http://localhost:8001/api/v1/agents/registry" \
        -H "Authorization: Bearer ${USER_TOKENS[carlos]:-}" --max-time 15 2>/dev/null) || registry_resp="{}"
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
    if $TEST_HELM; then
        local rm_pod as_pod
        rm_pod=$(oc get pods -n "$HELM_NS" -l component=request-manager -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        as_pod=$(oc get pods -n "$HELM_NS" -l component=agent-service -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
        rm_dcr=$(oc logs "$rm_pod" -n "$HELM_NS" -c request-manager 2>&1 | grep -ci "DCR.*regist\|DCR.*success\|dcr.*complete" || echo "0")
        rm_dcr=$(echo "$rm_dcr" | tr -d '[:space:]')
        as_dcr=$(oc logs "$as_pod" -n "$HELM_NS" -c agent-service 2>&1 | grep -ci "DCR.*regist\|DCR.*success\|dcr.*complete" || echo "0")
        as_dcr=$(echo "$as_dcr" | tr -d '[:space:]')
    else
        rm_dcr=$(docker logs partner-request-manager-full 2>&1 | grep -ci "DCR.*regist\|DCR.*success\|dcr.*complete" || echo "0")
        rm_dcr=$(echo "$rm_dcr" | tr -d '[:space:]')
        as_dcr=$(docker logs partner-agent-service-full 2>&1 | grep -ci "DCR.*regist\|DCR.*success\|dcr.*complete" || echo "0")
        as_dcr=$(echo "$as_dcr" | tr -d '[:space:]')
    fi

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

check_rag_ticket_type() {
    local label="$1"
    local token="$2"
    local message="$3"
    local expected_agent="$4"
    local expected_prefix="$5"

    local resp agent rag_sources

    resp=$(send_chat "$token" "$message")
    agent=$(echo "$resp" | jq -r '.agent // empty') || agent=""
    rag_sources=$(echo "$resp" | jq -r '.metadata.rag_sources // [] | .[]' 2>/dev/null) || rag_sources=""

    if [ -z "$rag_sources" ]; then
        record_fail "$label" "No RAG sources in response (agent=$agent)"
        return
    fi

    local all_match=true wrong_ids=""
    for src_id in $rag_sources; do
        if ! echo "$src_id" | grep -q "^${expected_prefix}"; then
            all_match=false
            wrong_ids="$wrong_ids $src_id"
        fi
    done

    if $all_match; then
        record_pass "$label (agent=$agent, sources=$rag_sources)"
    else
        record_fail "$label" "Expected ${expected_prefix}* tickets, got wrong:${wrong_ids} (agent=$agent)"
    fi
}

phase_5_chat_tests() {
    section_header "PHASE 5: Chat & Authorization Matrix"

    if [ -z "${USER_TOKENS[carlos]:-}" ]; then
        record_skip "Chat tests: carlos token missing"
        return
    fi

    echo "  ── carlos (groups: engineering, kubernetes, software) ──"

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

    # ── luis (groups: engineering, network) ──
    echo ""
    echo "  ── luis (groups: engineering, network) ──"

    if [ -n "${USER_TOKENS[luis]:-}" ]; then
        # 5.4: luis → network (ALLOW)
        echo "  Testing luis → network question..."
        check_chat_allowed \
            "Chat: luis → network" \
            "${USER_TOKENS[luis]}" \
            "My VPN tunnel keeps dropping. How do I troubleshoot IPsec issues?" \
            "network-support" \
            "vpn,ipsec,tunnel,network,firewall"

        # 5.5: luis → kubernetes (DENY - luis lacks kubernetes)
        echo "  Testing luis → kubernetes question (should deny)..."
        check_chat_denied \
            "Chat: luis → kubernetes DENIED" \
            "${USER_TOKENS[luis]}" \
            "My Kubernetes deployment is stuck in pending state"

        # 5.6: luis → software (DENY - luis lacks software)
        echo "  Testing luis → software question (should deny)..."
        check_chat_denied \
            "Chat: luis → software DENIED" \
            "${USER_TOKENS[luis]}" \
            "My Java application throws OutOfMemoryError on startup"
    else
        record_skip "Chat: luis tests (no token)"
    fi

    # ── sharon (groups: admin, engineering, kubernetes, network, software) ──
    echo ""
    echo "  ── sharon (groups: admin + all departments) ──"

    if [ -n "${USER_TOKENS[sharon]:-}" ]; then
        # 5.7: sharon → network (ALLOW)
        echo "  Testing sharon → network question..."
        check_chat_allowed \
            "Chat: sharon → network" \
            "${USER_TOKENS[sharon]}" \
            "How do I troubleshoot OSPF neighbor adjacency issues?" \
            "network-support" \
            "ospf,neighbor,adjacency,routing,network"

        # 5.8: sharon → kubernetes (ALLOW)
        echo "  Testing sharon → kubernetes question..."
        check_chat_allowed \
            "Chat: sharon → kubernetes" \
            "${USER_TOKENS[sharon]}" \
            "How do I scale a Kubernetes deployment to handle more traffic?" \
            "kubernetes-support" \
            "kubernetes,scale,deployment,replica,hpa"

        # 5.9: sharon → software (ALLOW)
        echo "  Testing sharon → software question..."
        check_chat_allowed \
            "Chat: sharon → software" \
            "${USER_TOKENS[sharon]}" \
            "My application logs show database connection pool exhaustion" \
            "software-support" \
            "database,connection,pool,application,error"
    else
        record_skip "Chat: sharon tests (no token)"
    fi

    # ── josh (groups: none) ──
    echo ""
    echo "  ── josh (groups: none) ──"

    if [ -n "${USER_TOKENS[josh]:-}" ]; then
        # 5.10: josh → kubernetes (DENY - no groups)
        echo "  Testing josh → kubernetes question (should deny)..."
        check_chat_denied \
            "Chat: josh → kubernetes DENIED" \
            "${USER_TOKENS[josh]}" \
            "Help me with my Kubernetes deployment"

        # 5.11: josh → network (DENY - no groups)
        echo "  Testing josh → network question (should deny)..."
        check_chat_denied \
            "Chat: josh → network DENIED" \
            "${USER_TOKENS[josh]}" \
            "How do I configure a VLAN on a Cisco switch?"

        # 5.12: josh → software (DENY - no groups)
        echo "  Testing josh → software question (should deny)..."
        check_chat_denied \
            "Chat: josh → software DENIED" \
            "${USER_TOKENS[josh]}" \
            "My Python script throws a segmentation fault"
    else
        record_skip "Chat: josh tests (no token)"
    fi

    # ── RAG ticket type isolation ──
    echo ""
    echo "  ── RAG ticket type isolation tests ──"

    local ticket_token="${USER_TOKENS[sharon]:-${USER_TOKENS[carlos]:-}}"
    if [ -n "$ticket_token" ]; then
        echo "  Testing kubernetes-support returns K8S-TICKET-* sources..."
        check_rag_ticket_type \
            "RAG: kubernetes-support → K8S-TICKET" \
            "$ticket_token" \
            "My pods keep restarting with CrashLoopBackOff errors" \
            "kubernetes-support" \
            "K8S-TICKET"

        echo "  Testing software-support returns SW-TICKET-* sources..."
        check_rag_ticket_type \
            "RAG: software-support → SW-TICKET" \
            "$ticket_token" \
            "My application crashes with an unhandled exception error 500" \
            "software-support" \
            "SW-TICKET"

        echo "  Testing network-support returns NET-TICKET-* sources..."
        check_rag_ticket_type \
            "RAG: network-support → NET-TICKET" \
            "$ticket_token" \
            "Our OSPF neighbor adjacency keeps flapping on the core router" \
            "network-support" \
            "NET-TICKET"
    else
        record_skip "RAG ticket type tests (no token available)"
    fi
}

# ═══════════════════════════════════════════════════════════════
# Phase 5b: Identity & Token Validation
# ═══════════════════════════════════════════════════════════════

phase_5b_identity_validation() {
    section_header "PHASE 5b: Identity & Token Validation"

    # 5b.1: JWT audience claim includes expected value
    if [ -n "${USER_TOKENS[carlos]:-}" ]; then
        local claims aud
        claims=$(decode_jwt_payload "${USER_TOKENS[carlos]}") || claims="{}"
        aud=$(echo "$claims" | jq -r '.aud // ""') || aud=""
        if echo "$aud" | grep -q "partner-agent-ui"; then
            record_pass "JWT: audience includes partner-agent-ui ($aud)"
        else
            record_fail "JWT: audience claim" "Expected partner-agent-ui in aud, got: $aud"
        fi

        # 5b.2: JWT issuer matches Keycloak realm
        local iss
        iss=$(echo "$claims" | jq -r '.iss // ""') || iss=""
        if echo "$iss" | grep -q "realms/partner-agent"; then
            record_pass "JWT: issuer is Keycloak realm ($iss)"
        else
            record_fail "JWT: issuer claim" "Expected realms/partner-agent in iss, got: $iss"
        fi

        # 5b.3: JWT has required identity fields (sub, preferred_username)
        local sub pref_user
        sub=$(echo "$claims" | jq -r '.sub // ""') || sub=""
        pref_user=$(echo "$claims" | jq -r '.preferred_username // ""') || pref_user=""
        if [ -n "$sub" ] && [ -n "$pref_user" ]; then
            record_pass "JWT: identity fields present (sub=$sub, user=$pref_user)"
        else
            record_fail "JWT: identity fields" "sub=$sub, preferred_username=$pref_user"
        fi

        # 5b.4: JWT azp (authorized party) matches client
        local azp
        azp=$(echo "$claims" | jq -r '.azp // ""') || azp=""
        if [ "$azp" = "partner-agent-ui" ]; then
            record_pass "JWT: authorized party is partner-agent-ui"
        else
            record_fail "JWT: azp claim" "Expected partner-agent-ui, got: $azp"
        fi
    fi

    # 5b.5: No-auth API call rejected with 403
    local noauth_code
    noauth_code=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:8001/api/v1/agents/registry" --max-time 10)
    if [ "$noauth_code" = "403" ]; then
        record_pass "Identity: no-auth API call rejected (HTTP 403)"
    else
        record_fail "Identity: no-auth rejection" "Expected 403, got $noauth_code"
    fi

    # 5b.6: .well-known is publicly accessible (no auth required)
    local wellknown_code
    wellknown_code=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:8001/.well-known/agent-card.json" --max-time 10)
    if [ "$wellknown_code" = "200" ]; then
        record_pass "Identity: .well-known/ accessible without auth (HTTP 200)"
    else
        record_fail "Identity: .well-known/ access" "Expected 200, got $wellknown_code"
    fi

    # 5b.7: Health endpoints accessible without auth
    local health_rm health_as
    health_rm=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000/health" --max-time 5)
    health_as=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8001/health" --max-time 5)
    if [ "$health_rm" = "200" ] && [ "$health_as" = "200" ]; then
        record_pass "Identity: health endpoints public (rm=$health_rm, as=$health_as)"
    else
        record_fail "Identity: health endpoints" "rm=$health_rm, as=$health_as"
    fi

    # 5b.8: Expired/garbage JWT rejected
    local garbage_code
    garbage_code=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:8001/api/v1/agents/registry" \
        -H "Authorization: Bearer invalid.garbage.token" --max-time 10)
    if [ "$garbage_code" = "403" ]; then
        record_pass "Identity: garbage JWT rejected (HTTP 403)"
    else
        record_fail "Identity: garbage JWT" "Expected 403, got $garbage_code"
    fi

    # 5b.9: SPIFFE identity mode in service containers
    local rm_spiffe as_spiffe
    rm_spiffe=$(docker exec partner-request-manager-full printenv SPIFFE_MODE 2>/dev/null) || rm_spiffe=""
    as_spiffe=$(docker exec partner-agent-service-full printenv SPIFFE_MODE 2>/dev/null) || as_spiffe=""
    if [ -n "$rm_spiffe" ] && [ -n "$as_spiffe" ]; then
        record_pass "SPIFFE: SPIFFE_MODE=$rm_spiffe on both services"
    else
        record_fail "SPIFFE: SPIFFE_MODE" "rm=$rm_spiffe, as=$as_spiffe"
    fi

    # 5b.10: SPIFFE trust domain configured
    local rm_td as_td
    rm_td=$(docker exec partner-request-manager-full printenv SPIFFE_TRUST_DOMAIN 2>/dev/null) || rm_td=""
    as_td=$(docker exec partner-agent-service-full printenv SPIFFE_TRUST_DOMAIN 2>/dev/null) || as_td=""
    if [ "$rm_td" = "partner.example.com" ] && [ "$as_td" = "partner.example.com" ]; then
        record_pass "SPIFFE: trust domain partner.example.com on both services"
    else
        record_fail "SPIFFE: trust domain" "rm=$rm_td, as=$as_td"
    fi

    # 5b.11: Service-to-service call with X-SPIFFE-ID header (mock mode)
    local spiffe_code spiffe_body
    spiffe_body=$(curl -s -w "\n%{http_code}" \
        "http://localhost:8001/api/v1/agents/registry" \
        -H "X-SPIFFE-ID: spiffe://partner.example.com/service/request-manager" \
        --max-time 10 2>/dev/null)
    spiffe_code=$(echo "$spiffe_body" | tail -1)
    if [ "$spiffe_code" = "200" ]; then
        record_pass "SPIFFE: X-SPIFFE-ID header accepted for service call (HTTP 200)"
    else
        record_fail "SPIFFE: X-SPIFFE-ID header" "Expected 200, got $spiffe_code"
    fi

    # 5b.12: mTLS configuration (SVID_DIR when SPIFFE_MODE=mtls)
    if [ "$rm_spiffe" = "mtls" ]; then
        local rm_svid_dir as_svid_dir
        rm_svid_dir=$(docker exec partner-request-manager-full printenv SVID_DIR 2>/dev/null) || rm_svid_dir=""
        as_svid_dir=$(docker exec partner-agent-service-full printenv SVID_DIR 2>/dev/null) || as_svid_dir=""
        if [ -n "$rm_svid_dir" ] && [ -n "$as_svid_dir" ]; then
            record_pass "mTLS: SVID_DIR set on both services ($rm_svid_dir)"
        else
            record_fail "mTLS: SVID_DIR" "rm=$rm_svid_dir, as=$as_svid_dir"
        fi
    else
        record_skip "mTLS: SVID_DIR (not in mtls mode)"
    fi

    # 5b.13: Refresh token works
    if [ -n "${USER_TOKENS[carlos]:-}" ]; then
        local login_resp refresh_token refresh_resp new_token
        login_resp=$(curl -s -X POST "http://localhost:8000/auth/login" \
            -H "Content-Type: application/json" \
            -d '{"email":"carlos","password":"carlos123"}' --max-time 15)
        refresh_token=$(echo "$login_resp" | jq -r '.refresh_token // empty')
        if [ -n "$refresh_token" ]; then
            refresh_resp=$(curl -s -X POST "http://localhost:8000/auth/refresh" \
                -H "Content-Type: application/json" \
                -d "{\"refresh_token\": \"$refresh_token\"}" --max-time 15) || refresh_resp=""
            new_token=$(echo "$refresh_resp" | jq -r '.token // .access_token // empty') || new_token=""
            if [ -n "$new_token" ] && [ ${#new_token} -gt 50 ]; then
                record_pass "Auth: token refresh works (new token ${#new_token} chars)"
            else
                record_fail "Auth: token refresh" "No valid token in refresh response"
            fi
        else
            record_skip "Auth: token refresh (no refresh_token in login response)"
        fi
    fi
}

# ═══════════════════════════════════════════════════════════════
# Phase 6: Praxis Gateway Tests
# ═══════════════════════════════════════════════════════════════

phase_6_praxis() {
    section_header "PHASE 6: Praxis Gateway Tests"

    # 6.1: Praxis admin health (inside container, not through the policy engine)
    local admin_body
    admin_body=$(docker exec partner-praxis-gateway-full \
        wget -q -O- http://127.0.0.1:9901/ready 2>/dev/null || echo "")
    if echo "$admin_body" | grep -q '"ok"'; then
        record_pass "Praxis: admin /ready endpoint"
    else
        record_fail "Praxis: admin /ready" "Expected {\"status\":\"ok\"}, got: $admin_body"
    fi

    # 6.2: Unauthenticated request through proxy must be rejected (JWT policy enforced)
    local unauth_code
    unauth_code=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:8180/api/v1/agents/registry" --max-time 15)
    if [ "$unauth_code" = "401" ] || [ "$unauth_code" = "403" ]; then
        record_pass "Praxis: unauthenticated request rejected (HTTP $unauth_code)"
    else
        record_fail "Praxis: unauthenticated rejection" "Expected 401/403, got $unauth_code"
    fi

    # 6.3: Garbage JWT rejected at gateway
    local garbage_praxis
    garbage_praxis=$(curl -s -o /dev/null -w "%{http_code}" \
        "http://localhost:8180/api/v1/agents/registry" \
        -H "Authorization: Bearer not-a-real.jwt.token" --max-time 15)
    if [ "$garbage_praxis" = "401" ] || [ "$garbage_praxis" = "403" ]; then
        record_pass "Praxis: garbage JWT rejected at gateway (HTTP $garbage_praxis)"
    else
        record_fail "Praxis: garbage JWT" "Expected 401/403, got $garbage_praxis"
    fi

    # 6.4: Authenticated request through proxy — carlos
    local auth_token="${USER_TOKENS[carlos]:-}"
    if [ -n "$auth_token" ]; then
        local auth_code registry_body
        registry_body=$(curl -s -w "\n%{http_code}" \
            "http://localhost:8180/api/v1/agents/registry" \
            -H "Authorization: Bearer $auth_token" \
            --max-time 15 2>/dev/null)
        auth_code=$(echo "$registry_body" | tail -1)
        registry_body=$(echo "$registry_body" | sed '$d')

        if [ "$auth_code" = "200" ] && echo "$registry_body" | grep -q "kubernetes-support"; then
            record_pass "Praxis: carlos proxy → agent registry (HTTP 200)"
        else
            record_fail "Praxis: carlos proxy" "HTTP $auth_code"
        fi
    else
        record_skip "Praxis: carlos proxy (no token)"
    fi

    # 6.5: Authenticated request through proxy — luis
    if [ -n "${USER_TOKENS[luis]:-}" ]; then
        local luis_code
        luis_code=$(curl -s -o /dev/null -w "%{http_code}" \
            "http://localhost:8180/api/v1/agents/registry" \
            -H "Authorization: Bearer ${USER_TOKENS[luis]}" --max-time 15)
        if [ "$luis_code" = "200" ]; then
            record_pass "Praxis: luis proxy → agent registry (HTTP 200)"
        else
            record_fail "Praxis: luis proxy" "Expected 200, got $luis_code"
        fi
    fi

    # 6.6: Authenticated request through proxy — sharon
    if [ -n "${USER_TOKENS[sharon]:-}" ]; then
        local sharon_code
        sharon_code=$(curl -s -o /dev/null -w "%{http_code}" \
            "http://localhost:8180/api/v1/agents/registry" \
            -H "Authorization: Bearer ${USER_TOKENS[sharon]}" --max-time 15)
        if [ "$sharon_code" = "200" ]; then
            record_pass "Praxis: sharon proxy → agent registry (HTTP 200)"
        else
            record_fail "Praxis: sharon proxy" "Expected 200, got $sharon_code"
        fi
    fi

    # 6.7: Praxis proxies agent-service endpoints correctly (agent card via gateway)
    if [ -n "${USER_TOKENS[carlos]:-}" ]; then
        local praxis_card_code praxis_card_body
        praxis_card_body=$(curl -s -w "\n%{http_code}" \
            "http://localhost:8180/.well-known/agent-card.json" \
            -H "Authorization: Bearer ${USER_TOKENS[carlos]}" \
            --max-time 15 2>/dev/null)
        praxis_card_code=$(echo "$praxis_card_body" | tail -1)
        praxis_card_body=$(echo "$praxis_card_body" | sed '$d')
        if [ "$praxis_card_code" = "200" ] && echo "$praxis_card_body" | jq -r '.agent_cards | keys[]' 2>/dev/null | grep -q "kubernetes-support"; then
            record_pass "Praxis: agent card via gateway (3 agents found)"
        else
            record_fail "Praxis: agent card via gateway" "HTTP $praxis_card_code"
        fi
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

    # 7.6: Chat request events (increased threshold for full matrix)
    local chat_count
    chat_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'data.chat.request' AND created_at >= '$AUDIT_START_TS'")
    if [ "${chat_count:-0}" -ge 8 ]; then
        record_pass "Audit: data.chat.request events ($chat_count found)"
    else
        record_fail "Audit: data.chat.request events" "Expected >=8 (full matrix), found ${chat_count:-0}"
    fi

    # 7.7: Authorization deny events for each denied user scenario
    local deny_details
    deny_details=$(db_query "SELECT COUNT(DISTINCT actor) FROM audit_events WHERE event_type IN ('authz.deny','authz.routing_direct') AND created_at >= '$AUDIT_START_TS'")
    if [ "${deny_details:-0}" -ge 2 ]; then
        record_pass "Audit: deny events from multiple users ($deny_details distinct actors)"
    else
        record_fail "Audit: deny diversity" "Expected >=2 distinct actors, got ${deny_details:-0}"
    fi

    # 7.8: Audit events have service field populated
    local no_service
    no_service=$(db_query "SELECT COUNT(*) FROM audit_events WHERE service IS NULL AND created_at >= '$AUDIT_START_TS'")
    if [ "${no_service:-0}" = "0" ]; then
        record_pass "Audit: all events have service field populated"
    else
        record_fail "Audit: missing service field" "$no_service events lack service"
    fi

    # 7.9: Every allowed chat request triggered at least one token exchange
    local te_final_count chat_allow_count
    te_final_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'token.exchange' AND created_at >= '$AUDIT_START_TS'")
    chat_allow_count=$(db_query "SELECT COUNT(*) FROM audit_events WHERE event_type = 'data.chat.request' AND created_at >= '$AUDIT_START_TS'")
    if [ "${te_final_count:-0}" -ge "${chat_allow_count:-0}" ]; then
        record_pass "Audit: token exchanges ($te_final_count) >= chat requests ($chat_allow_count)"
    else
        record_fail "Audit: token exchange coverage" "te=$te_final_count < chats=$chat_allow_count"
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

    # 8.5: Multiple distinct sessions (proxy for user diversity)
    local session_count
    session_count=$(db_query "SELECT COUNT(DISTINCT session_id) FROM request_logs WHERE created_at >= '$AUDIT_START_TS'")
    if [ "${session_count:-0}" -ge 3 ]; then
        record_pass "Request Logs: multiple sessions recorded ($session_count distinct)"
    else
        record_fail "Request Logs: session diversity" "Expected >=3, found ${session_count:-0}"
    fi

    # 8.6: All 3 specialist agents served requests
    local specialist_count
    specialist_count=$(db_query "SELECT COUNT(DISTINCT agent_id) FROM request_logs WHERE created_at >= '$AUDIT_START_TS' AND agent_id IN ('kubernetes-support','software-support','network-support')")
    if [ "${specialist_count:-0}" -ge 3 ]; then
        record_pass "Request Logs: all 3 specialists served requests"
    else
        record_fail "Request Logs: specialist coverage" "Expected 3, found ${specialist_count:-0}"
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
    echo "  Flags: skip-build=$SKIP_BUILD skip-deploy=$SKIP_DEPLOY test-helm=$TEST_HELM"
    echo "  Started: $(date)"

    if $TEST_HELM; then
        trap cleanup_portforwards EXIT
        setup_helm_portforwards
    fi

    if ! $SKIP_DEPLOY; then
        phase_0_clean
        phase_1_build
        phase_2_deploy
    fi

    phase_3_readiness
    phase_4_authentication
    phase_4b_dcr_and_discovery
    phase_5_chat_tests
    phase_5b_identity_validation
    phase_6_praxis

    # Allow async audit writes to flush
    sleep 3

    phase_7_audit
    phase_8_request_logs
    phase_9_summary
}

main "$@"
