#!/bin/bash
# Start ALL application services with proper configuration
# Called by: scripts/setup.sh
# Requires: CLIENT_SECRET environment variable

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

echo "════════════════════════════════════════════════════════════"
echo "STARTING APPLICATION SERVICES"
echo "════════════════════════════════════════════════════════════"
echo ""

# Verify CLIENT_SECRET is set
if [ -z "$CLIENT_SECRET" ]; then
    echo "ERROR: CLIENT_SECRET environment variable not set!" >&2
    exit 1
fi

# Get configuration
KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8090}"
REALM="${REALM:-partner-agent}"
DB_URL="${DATABASE_URL:-postgresql+asyncpg://user:pass@partner-postgres-full:5432/partner_agent}"
GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"

# DCR Initial Access Token — written by seed-keycloak.sh
# Agents use this to self-register with Keycloak on startup (RFC 7591).
DCR_ENABLED="${DCR_ENABLED:-true}"
if [ -f /tmp/dcr_initial_access_token.txt ]; then
    DCR_IAT=$(cat /tmp/dcr_initial_access_token.txt)
    echo "  DCR enabled — using Initial Access Token from seed-keycloak.sh"
elif [ -n "${KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN:-}" ]; then
    DCR_IAT="${KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN}"
    echo "  DCR enabled — using KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN from environment"
else
    DCR_IAT=""
    echo "  WARNING: DCR_ENABLED=true but no Initial Access Token found."
    echo "           Run seed-keycloak.sh first, or set KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN."
    echo "           Agents will fall back to legacy client_secret auth."
fi

# =============================================================================
# 1. Run Database Migrations
# =============================================================================
echo "[1/5] Running database migrations..."
docker run --rm --name partner-db-migrate \
    --network partner-agent-network \
    -e DATABASE_URL="$DB_URL" \
    -w /app \
    partner-request-manager:latest \
    bash -c "cd /app/shared-models && python3 -m alembic upgrade head" 2>&1 | grep -E "INFO|upgrade|ERROR" || true
echo "  OK Migrations complete"
echo ""

# =============================================================================
# 2. RAG API
# =============================================================================
echo "[2/5] Starting RAG API..."
docker stop partner-rag-api-full 2>/dev/null || true
docker rm partner-rag-api-full 2>/dev/null || true

docker run -d \
    --name partner-rag-api-full \
    --network partner-agent-network \
    -p 8080:8080 \
    -e "GOOGLE_API_KEY=$GOOGLE_API_KEY" \
    -e "DATABASE_URL=$DB_URL" \
    -e "EMBEDDING_MODEL=models/gemini-embedding-001" \
    -e "LLM_MODEL=gemini-2.5-flash" \
    partner-rag-api:latest > /dev/null

echo "  OK RAG API started"
sleep 3
echo ""

# =============================================================================
# 3. Agent Service (using shared SPIRE Agent socket)
# =============================================================================
echo "[3/5] Starting Agent Service..."

docker stop partner-agent-service-full 2>/dev/null || true
docker rm partner-agent-service-full 2>/dev/null || true

docker run -d \
    --name partner-agent-service-full \
    --network partner-agent-network \
    --label "com.docker.compose.service=agent-service" \
    -p 8001:8080 \
    -v spire-socket:/run/spire/sockets:ro \
    -e "DATABASE_URL=$DB_URL" \
    -e "LLM_BACKEND=gemini" \
    -e "GOOGLE_API_KEY=$GOOGLE_API_KEY" \
    -e "GEMINI_MODEL=gemini-2.5-flash" \
    -e "LOG_LEVEL=INFO" \
    -e "RAG_API_ENDPOINT=http://partner-rag-api-full:8080/answer" \
    -e "SPIFFE_TRUST_DOMAIN=partner.example.com" \
    -e "OPA_URL=http://partner-opa-full:8181" \
    -e "SPIFFE_ENDPOINT_SOCKET=/run/spire/sockets/agent.sock" \
    -e "KEYCLOAK_URL=http://partner-keycloak-full:8090" \
    -e "KEYCLOAK_REALM=partner-agent" \
    -e "GATEWAY_BASE_URL=http://partner-agent-service-full:8080" \
    -e "DCR_ENABLED=${DCR_ENABLED}" \
    -e "KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN=${DCR_IAT}" \
    -e "KEYCLOAK_DCR_ENDPOINT=http://partner-keycloak-full:8090/realms/partner-agent/clients-registrations/openid-connect" \
    -e "KEYCLOAK_ADMIN_USERNAME=${KEYCLOAK_ADMIN_USERNAME:-admin}" \
    -e "KEYCLOAK_ADMIN_PASSWORD=${KEYCLOAK_ADMIN_PASSWORD:-admin123}" \
    partner-agent-service:latest > /dev/null

echo "  OK Agent Service started"
sleep 3
echo ""

# =============================================================================
# 4. Request Manager (using shared SPIRE Agent socket)
# =============================================================================
echo "[4/5] Starting Request Manager..."

docker stop partner-request-manager-full 2>/dev/null || true
docker rm partner-request-manager-full 2>/dev/null || true

docker run -d \
    --name partner-request-manager-full \
    --network partner-agent-network \
    --label "com.docker.compose.service=request-manager" \
    -p 8000:8080 \
    -v spire-socket:/run/spire/sockets:ro \
    -e "DATABASE_URL=$DB_URL" \
    -e "LLM_BACKEND=gemini" \
    -e "GOOGLE_API_KEY=$GOOGLE_API_KEY" \
    -e "GEMINI_MODEL=gemini-2.5-flash" \
    -e "AGENT_SERVICE_URL=http://partner-agent-service-full:8080" \
    -e "AGENT_TIMEOUT=120" \
    -e "LOG_LEVEL=INFO" \
    -e "STRUCTURED_CONTEXT_ENABLED=true" \
    -e "SPIFFE_TRUST_DOMAIN=partner.example.com" \
    -e "OPA_URL=http://partner-opa-full:8181" \
    -e "KEYCLOAK_URL=http://partner-keycloak-full:8090" \
    -e "KEYCLOAK_REALM=$REALM" \
    -e "KEYCLOAK_CLIENT_ID=partner-agent-ui" \
    -e "KEYCLOAK_CLIENT_SECRET=$CLIENT_SECRET" \
    -e "SPIFFE_ENDPOINT_SOCKET=/run/spire/sockets/agent.sock" \
    -e "REGISTRY_TTL_SECONDS=${REGISTRY_TTL_SECONDS:-300}" \
    -e "AGENT_CARD_DISCOVERY=${AGENT_CARD_DISCOVERY:-false}" \
    -e "DCR_ENABLED=${DCR_ENABLED}" \
    -e "KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN=${DCR_IAT}" \
    -e "KEYCLOAK_DCR_ENDPOINT=http://partner-keycloak-full:8090/realms/partner-agent/clients-registrations/openid-connect" \
    -e "KEYCLOAK_ADMIN_USERNAME=${KEYCLOAK_ADMIN_USERNAME:-admin}" \
    -e "KEYCLOAK_ADMIN_PASSWORD=${KEYCLOAK_ADMIN_PASSWORD:-admin123}" \
    partner-request-manager:latest > /dev/null

echo "  OK Request Manager started"
sleep 3
echo ""

# =============================================================================
# 5. Web UI
# =============================================================================
echo "[5/5] Starting Web UI..."
docker stop partner-pf-chat-ui-full 2>/dev/null || true
docker rm partner-pf-chat-ui-full 2>/dev/null || true

docker run -d \
    --name partner-pf-chat-ui-full \
    --network partner-agent-network \
    -p 3000:8080 \
    partner-pf-chat-ui:latest > /dev/null

echo "  OK Web UI started"
sleep 2
echo ""

# =============================================================================
# 6. Register SPIRE Workloads (using Docker labels)
# =============================================================================
echo "[6/6] Registering SPIRE workloads..."

# Wait for SPIRE Agent to fully attest before registering workloads.
sleep 3

# Discover the live SPIRE agent's SPIFFE ID from the server.
# The join-token changes on every container restart, so we cannot
# hard-code /agent/main — we must look up the actual attested agent.
LIVE_AGENT_ID=$(docker exec spire-server \
    /opt/spire/bin/spire-server agent list 2>/dev/null \
    | grep "SPIFFE ID" | tail -1 | sed 's/.*: //' | tr -d '[:space:]')

if [ -z "$LIVE_AGENT_ID" ]; then
    echo "  ✗ No live SPIRE agent found — workload entries cannot be created"
    echo "    Check: docker logs partner-spire-agent"
    exit 1
fi
echo "  Live SPIRE agent: ${LIVE_AGENT_ID}"

# Helper: register a workload entry, replacing any stale entry for the
# same SPIFFE ID that points to a different (old) parent.
register_workload() {
    local SPIFFE_ID="$1"
    local SELECTOR="$2"

    # Find any existing entry for this SPIFFE ID
    EXISTING=$(docker exec spire-server \
        /opt/spire/bin/spire-server entry show \
        -spiffeID "$SPIFFE_ID" 2>/dev/null | grep "^Entry ID" | awk '{print $3}')

    if [ -n "$EXISTING" ]; then
        # Check if its parent matches the live agent — if not, delete it
        EXISTING_PARENT=$(docker exec spire-server \
            /opt/spire/bin/spire-server entry show \
            -spiffeID "$SPIFFE_ID" 2>/dev/null | grep "^Parent ID" | sed 's/Parent ID.*: //' | tr -d '[:space:]')

        if [ "$EXISTING_PARENT" = "$LIVE_AGENT_ID" ]; then
            echo "  - Entry exists with correct parent: ${SPIFFE_ID}"
            return 0
        fi

        echo "  ! Stale entry (wrong parent) — deleting and re-creating: ${SPIFFE_ID}"
        docker exec spire-server \
            /opt/spire/bin/spire-server entry delete \
            -entryID "$EXISTING" >/dev/null 2>&1
    fi

    docker exec spire-server \
        /opt/spire/bin/spire-server entry create \
        -parentID "$LIVE_AGENT_ID" \
        -spiffeID "$SPIFFE_ID" \
        -selector "$SELECTOR" \
        -ttl 86400 >/dev/null 2>&1 \
    && echo "  ✓ Registered: ${SPIFFE_ID}" \
    || echo "  ✗ Failed to register: ${SPIFFE_ID}"
}

register_workload \
    "spiffe://partner.example.com/request-manager" \
    "docker:label:com.docker.compose.service:request-manager"

register_workload \
    "spiffe://partner.example.com/agent-service" \
    "docker:label:com.docker.compose.service:agent-service"

echo ""
echo "════════════════════════════════════════════════════════════"
echo "OK ALL SERVICES STARTED & REGISTERED"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Running services:"
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'NAMES|partner|spire'
echo ""
echo "Service URLs:"
echo "  • Web UI:          http://localhost:3000"
echo "  • Request Manager: http://localhost:8000"
echo "  • Agent Service:   http://localhost:8001"
echo "  • RAG API:         http://localhost:8080"
echo "  • Keycloak:        http://localhost:8090"
echo "  • OPA:             http://localhost:8181"
echo ""
