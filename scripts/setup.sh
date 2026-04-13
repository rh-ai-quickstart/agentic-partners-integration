#!/bin/bash
# Complete setup - builds, starts, and initializes everything

set -e

echo "════════════════════════════════════════════════════════════"
echo "Partner Agent System - Complete Setup"
echo "════════════════════════════════════════════════════════════"
echo ""

# Get project root (parent of scripts/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Auto-load .env if GOOGLE_API_KEY not set
if [ -z "$GOOGLE_API_KEY" ] && [ -f ".env" ]; then
    echo "Loading environment from .env..."
    source .env
fi

# Prompt for GOOGLE_API_KEY if still not set
if [ -z "$GOOGLE_API_KEY" ]; then
    echo "GOOGLE_API_KEY is not set."
    echo ""
    read -rp "Enter your Google API key: " GOOGLE_API_KEY
    if [ -z "$GOOGLE_API_KEY" ]; then
        echo "ERROR: GOOGLE_API_KEY is required."
        exit 1
    fi
    # Save to .env so future runs pick it up automatically
    echo "GOOGLE_API_KEY=${GOOGLE_API_KEY}" >> .env
    echo "Saved to .env"
fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ============================================
# 1. BUILD CONTAINERS (skip if SKIP_BUILD=true)
# ============================================
if [ "${SKIP_BUILD}" = "true" ]; then
    echo -e "${YELLOW}Skipping build (SKIP_BUILD=true)${NC}"
else
    echo -e "${YELLOW}Building containers...${NC}"
    bash scripts/build_containers.sh
fi

# ============================================
# 2. START INFRASTRUCTURE
# ============================================
echo ""
echo -e "${YELLOW}Starting infrastructure...${NC}"

# Create network
docker network inspect partner-agent-network > /dev/null 2>&1 || \
    docker network create partner-agent-network

# Start PostgreSQL
docker rm -f partner-postgres-full 2>/dev/null || true
docker run -d \
    --name partner-postgres-full \
    --network partner-agent-network \
    -e POSTGRES_USER=user \
    -e POSTGRES_PASSWORD=pass \
    -e POSTGRES_DB=partner_agent \
    -p 5433:5432 \
    pgvector/pgvector:pg16
echo "  PostgreSQL started"

# Start ChromaDB
docker rm -f partner-chromadb-full 2>/dev/null || true
docker run -d \
    --name partner-chromadb-full \
    --network partner-agent-network \
    -p 8002:8000 \
    chromadb/chroma:latest
echo "  ChromaDB started"

# Start Keycloak (OIDC Identity Provider)
docker rm -f partner-keycloak-full 2>/dev/null || true
docker run -d \
    --name partner-keycloak-full \
    --network partner-agent-network \
    -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
    -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin123 \
    -e KC_HTTP_ENABLED=true \
    -e KC_HEALTH_ENABLED=true \
    -v "${PROJECT_ROOT}/keycloak/realm-partner.json:/opt/keycloak/data/import/realm-partner.json:ro" \
    -p 8090:8080 \
    -p 9090:9000 \
    quay.io/keycloak/keycloak:26.5 \
    start-dev --import-realm
echo "  Keycloak started"

# Start OPA (Open Policy Agent)
docker rm -f partner-opa-full 2>/dev/null || true
docker run -d \
    --name partner-opa-full \
    --network partner-agent-network \
    -v "${PROJECT_ROOT}/policies:/policies:ro" \
    -p 8181:8181 \
    openpolicyagent/opa:latest \
    run --server --addr :8181 /policies
echo "  OPA started"

# Wait for databases
echo "  Waiting for PostgreSQL to be ready..."
for i in {1..30}; do
    if docker exec partner-postgres-full pg_isready -U user -d partner_agent >/dev/null 2>&1; then
        echo "  PostgreSQL ready"
        break
    fi
    sleep 1
    if [ $i -eq 30 ]; then
        echo "  PostgreSQL failed to start"
        exit 1
    fi
done

# Wait for Keycloak
echo "  Waiting for Keycloak to be ready (this takes ~30-60s)..."
for i in {1..90}; do
    if curl -sf http://localhost:9090/health/ready > /dev/null 2>&1; then
        echo "  Keycloak ready"
        break
    fi
    sleep 2
    if [ $i -eq 90 ]; then
        echo "  Keycloak failed to start"
        exit 1
    fi
done

# Wait for OPA
echo "  Waiting for OPA to be ready..."
for i in {1..15}; do
    if curl -sf http://localhost:8181/health > /dev/null 2>&1; then
        echo "  OPA ready"
        break
    fi
    sleep 1
    if [ $i -eq 15 ]; then
        echo "  OPA failed to start"
        exit 1
    fi
done

# ============================================
# 3. RUN DATABASE MIGRATIONS
# ============================================
echo ""
echo -e "${YELLOW}Running database migrations...${NC}"
docker run --rm \
    --name partner-migrations-temp \
    --network partner-agent-network \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-full:5432/partner_agent \
    -w /app/shared-models \
    partner-request-manager:latest \
    python3 -m alembic upgrade head

echo "  Migrations complete"

# ============================================
# 4. START SERVICES
# ============================================
echo ""
echo -e "${YELLOW}Starting services...${NC}"

# Agent Service
docker rm -f partner-agent-service-full 2>/dev/null || true
docker run -d \
    --name partner-agent-service-full \
    --network partner-agent-network \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-full:5432/partner_agent \
    -e LLM_BACKEND=gemini \
    -e GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
    -e GEMINI_MODEL=gemini-2.5-flash \
    -e LOG_LEVEL=INFO \
    -e RAG_API_ENDPOINT=http://partner-rag-api-full:8080/answer \
    -e MOCK_SPIFFE=true \
    -e SPIFFE_TRUST_DOMAIN=partner.example.com \
    -e OPA_URL=http://partner-opa-full:8181 \
    -p 8001:8080 \
    partner-agent-service:latest
echo "  Agent service starting..."

# Request Manager
docker rm -f partner-request-manager-full 2>/dev/null || true
docker run -d \
    --name partner-request-manager-full \
    --network partner-agent-network \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-full:5432/partner_agent \
    -e LLM_BACKEND=gemini \
    -e GOOGLE_API_KEY="${GOOGLE_API_KEY}" \
    -e GEMINI_MODEL=gemini-2.5-flash \
    -e AGENT_SERVICE_URL=http://partner-agent-service-full:8080 \
    -e AGENT_TIMEOUT=120 \
    -e LOG_LEVEL=INFO \
    -e STRUCTURED_CONTEXT_ENABLED=true \
    -e MOCK_SPIFFE=true \
    -e SPIFFE_TRUST_DOMAIN=partner.example.com \
    -e OPA_URL=http://partner-opa-full:8181 \
    -e KEYCLOAK_URL=http://partner-keycloak-full:8080 \
    -e KEYCLOAK_REALM=partner-agent \
    -e KEYCLOAK_CLIENT_ID=partner-agent-ui \
    -p 8000:8080 \
    partner-request-manager:latest
echo "  Request manager starting..."

# RAG API (must have GOOGLE_API_KEY for embeddings)
docker rm -f partner-rag-api-full 2>/dev/null || true
docker run -d \
    --name partner-rag-api-full \
    --network partner-agent-network \
    -e "GOOGLE_API_KEY=${GOOGLE_API_KEY}" \
    -e CHROMA_HOST=partner-chromadb-full \
    -e CHROMA_PORT=8000 \
    -e EMBEDDING_MODEL=models/gemini-embedding-001 \
    -e LLM_MODEL=gemini-2.5-flash \
    -p 8003:8080 \
    partner-rag-api:latest
echo "  RAG API starting..."

# Wait for all services to be ready
echo "  Waiting for services to be ready..."
AGENT_READY=false
RM_READY=false
RAG_READY=false
for i in {1..60}; do
    if [ "$AGENT_READY" = false ] && curl -sf http://localhost:8001/health > /dev/null 2>&1; then
        echo "  Agent service ready"
        AGENT_READY=true
    fi
    if [ "$RM_READY" = false ] && curl -sf http://localhost:8000/health > /dev/null 2>&1; then
        echo "  Request manager ready"
        RM_READY=true
    fi
    if [ "$RAG_READY" = false ] && curl -sf http://localhost:8003/health > /dev/null 2>&1; then
        echo "  RAG API ready"
        RAG_READY=true
    fi
    if [ "$AGENT_READY" = true ] && [ "$RM_READY" = true ] && [ "$RAG_READY" = true ]; then
        break
    fi
    sleep 2
done

if [ "$AGENT_READY" = false ] || [ "$RM_READY" = false ] || [ "$RAG_READY" = false ]; then
    echo -e "  ${YELLOW}WARNING: Some services failed to start:${NC}"
    [ "$AGENT_READY" = false ] && echo "    - Agent service (port 8001)"
    [ "$RM_READY" = false ] && echo "    - Request manager (port 8000)"
    [ "$RAG_READY" = false ] && echo "    - RAG API (port 8003)"
    echo "  Check logs: docker logs <container-name>"
fi

# ============================================
# 5. INITIALIZE DATA
# ============================================
echo ""
echo -e "${YELLOW}Initializing data...${NC}"

# Ingest RAG knowledge
docker cp rag-service/ingest_knowledge.py partner-rag-api-full:/app/
docker cp data partner-rag-api-full:/app/ 2>/dev/null || true

echo "  Ingesting RAG knowledge..."
INGEST_OK=false
for attempt in 1 2 3; do
    if docker exec -e "GOOGLE_API_KEY=${GOOGLE_API_KEY}" partner-rag-api-full python /app/ingest_knowledge.py 2>&1; then
        INGEST_OK=true
        break
    fi
    echo "  Ingestion attempt $attempt failed, retrying in 10s..."
    sleep 10
done
if [ "$INGEST_OK" = true ]; then
    echo "  RAG knowledge ingested"
else
    echo -e "  ${YELLOW}WARNING: RAG ingestion failed after 3 attempts. RAG queries may return empty results.${NC}"
    echo "  You can retry manually: source .env && docker exec -e GOOGLE_API_KEY=\${GOOGLE_API_KEY} partner-rag-api-full python /app/ingest_knowledge.py"
fi

# Start PF Chat UI
echo "  Starting PF Chat UI..."
docker rm -f partner-pf-chat-ui 2>/dev/null || true
docker run -d \
    --name partner-pf-chat-ui \
    --network partner-agent-network \
    -p 3000:8080 \
    partner-pf-chat-ui:latest
echo "  PF Chat UI started"

# ============================================
# 6. VERIFY & DONE
# ============================================
echo ""
echo -e "${YELLOW}Verifying setup...${NC}"

CHECKS_PASSED=0
CHECKS_TOTAL=6

# Check each service
for svc in "PostgreSQL:partner-postgres-full:5433" "Keycloak:localhost:9090/health/ready" "OPA:localhost:8181/health"; do
    name="${svc%%:*}"
    if echo "$svc" | grep -q "postgres"; then
        docker exec partner-postgres-full pg_isready -U user -d partner_agent >/dev/null 2>&1 && \
            CHECKS_PASSED=$((CHECKS_PASSED + 1)) && echo -e "  ${GREEN}OK${NC}  $name" || echo -e "  FAIL  $name"
    else
        endpoint="${svc#*:}"
        curl -sf "http://$endpoint" > /dev/null 2>&1 && \
            CHECKS_PASSED=$((CHECKS_PASSED + 1)) && echo -e "  ${GREEN}OK${NC}  $name" || echo -e "  FAIL  $name"
    fi
done
for svc in "Request Manager:8000" "Agent Service:8001" "RAG API:8003"; do
    name="${svc%%:*}"
    port="${svc#*:}"
    curl -sf "http://localhost:$port/health" > /dev/null 2>&1 && \
        CHECKS_PASSED=$((CHECKS_PASSED + 1)) && echo -e "  ${GREEN}OK${NC}  $name" || echo -e "  FAIL  $name"
done

# Check RAG data
RAG_DOCS=$(curl -sf http://localhost:8003/collections 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(sum(c['count'] for c in d['collections']))" 2>/dev/null || echo "0")
if [ "$RAG_DOCS" -gt 0 ] 2>/dev/null; then
    echo -e "  ${GREEN}OK${NC}  RAG knowledge base ($RAG_DOCS documents)"
else
    echo -e "  ${YELLOW}WARN${NC}  RAG knowledge base is empty"
fi

echo ""
echo "════════════════════════════════════════════════════════════"
if [ "$CHECKS_PASSED" -eq "$CHECKS_TOTAL" ]; then
    echo -e "${GREEN}Setup Complete! All $CHECKS_TOTAL services running.${NC}"
else
    echo -e "${YELLOW}Setup Complete ($CHECKS_PASSED/$CHECKS_TOTAL services running)${NC}"
fi
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Open the Web UI to get started:"
echo ""
echo -e "  ${GREEN}http://localhost:3000${NC}"
echo ""
echo "Login with one of these test users:"
echo ""
echo "  Email                    Password     Departments"
echo "  ───────────────────────  ───────────  ──────────────────────"
echo "  carlos@example.com       carlos123    software"
echo "  luis@example.com         luis123       network"
echo "  sharon@example.com       sharon123    software, network (admin)"
echo "  josh@example.com         josh123      (none - access denied)"
echo ""
echo "Each user can only chat with agents matching their departments."
echo "Authorization is enforced by OPA policy (departments x agent capabilities)."
echo ""
echo "Other Services:"
echo "  API:        http://localhost:8000    Request Manager"
echo "  Agent:      http://localhost:8001    Agent Service"
echo "  RAG:        http://localhost:8003    Knowledge Base API"
echo "  Keycloak:   http://localhost:8090    Identity Provider (admin/admin123)"
echo "  OPA:        http://localhost:8181    Policy Engine"
echo ""
echo "Commands:"
echo "  make test          Run E2E tests against running services"
echo "  make test-unit     Run unit tests (no containers needed)"
echo "  make stop          Stop all containers"
echo "  make clean         Stop and remove all containers"
echo "  make logs-*        Tail service logs (e.g. make logs-request-manager)"
echo ""
