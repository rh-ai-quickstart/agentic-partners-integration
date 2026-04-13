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
    echo "Loading GOOGLE_API_KEY from .env..."
    source .env
fi

# Check prerequisites
if [ -z "$GOOGLE_API_KEY" ]; then
    echo "ERROR: GOOGLE_API_KEY not set"
    echo ""
    echo "Set it first:"
    echo "  export GOOGLE_API_KEY=your-api-key"
    exit 1
fi

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ============================================
# 1. BUILD CONTAINERS (FRESH)
# ============================================
echo -e "${YELLOW}Building fresh containers...${NC}"
bash scripts/build_containers.sh

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

# Wait for services
echo "  Waiting for services to be ready..."
for i in {1..30}; do
    if curl -s http://localhost:8001/health > /dev/null 2>&1 && \
       curl -s http://localhost:8000/health > /dev/null 2>&1; then
        break
    fi
    sleep 2
done

# ============================================
# 5. INITIALIZE DATA
# ============================================
echo ""
echo -e "${YELLOW}Initializing data...${NC}"

# Ingest RAG knowledge
docker cp rag-service/ingest_knowledge.py partner-rag-api-full:/app/
docker cp data partner-rag-api-full:/app/ 2>/dev/null || true

echo "  Ingesting RAG knowledge..."
docker exec -e GOOGLE_API_KEY="${GOOGLE_API_KEY}" partner-rag-api-full python /app/ingest_knowledge.py > /dev/null 2>&1 || true
echo "  RAG knowledge ingested"

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
# 6. DONE
# ============================================
echo ""
echo "════════════════════════════════════════════════════════════"
echo -e "${GREEN}Setup Complete!${NC}"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Services Running:"
echo "  Web UI:     http://localhost:3000"
echo "  API:        http://localhost:8000"
echo "  Agent:      http://localhost:8001"
echo "  RAG API:    http://localhost:8003"
echo "  Keycloak:   http://localhost:8090 (admin/admin123)"
echo "  OPA:        http://localhost:8181"
echo ""
echo "Login Credentials (Keycloak):"
echo "  carlos@example.com / carlos123 (Software dept)"
echo "  luis@example.com / luis123 (Network dept)"
echo "  sharon@example.com / sharon123 (All depts - Admin)"
echo "  josh@example.com / josh123 (No dept access)"
echo ""
echo "Users are managed in Keycloak. DB records are auto-created"
echo "on first login from Keycloak JWT claims."
echo ""
echo "Next Steps:"
echo "  Test: bash scripts/test.sh"
echo "  Logs: docker logs -f partner-request-manager-full"
echo "  Stop: bash scripts/stop.sh"
echo ""
