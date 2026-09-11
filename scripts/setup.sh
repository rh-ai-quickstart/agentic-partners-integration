#!/bin/bash
# Complete Setup Script - Single command to set up everything
# Usage: bash scripts/setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
SEED_DIR="$SCRIPT_DIR/seed"

echo "════════════════════════════════════════════════════════════"
echo "  PARTNER AGENT INTEGRATION - COMPLETE SETUP"
echo "════════════════════════════════════════════════════════════"
echo ""

# Load environment from .env
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo "Loading environment from .env..."
    source "$PROJECT_ROOT/.env"
fi

# Verify required environment variables
if [ -z "$GOOGLE_API_KEY" ]; then
    echo "ERROR: GOOGLE_API_KEY not set in .env"
    exit 1
fi

cd "$PROJECT_ROOT"

# =============================================================================
# STEP 1: Rebuild Containers (use cache for speed)
# =============================================================================
echo ""
echo "[1/8] Rebuilding application containers (using cache)..."
if command -v docker &> /dev/null; then
    echo "  - Building request-manager..."
    docker build -t partner-request-manager:latest -f request-manager/Containerfile . > /tmp/build-request-manager.log 2>&1
    if [ $? -eq 0 ]; then
        echo "  ✓ Request Manager built"
    else
        echo "  ✗ Request Manager build FAILED - check /tmp/build-request-manager.log"
        tail -20 /tmp/build-request-manager.log
        exit 1
    fi

    echo "  - Building agent-service..."
    docker build -t partner-agent-service:latest -f agent-service/Containerfile . > /tmp/build-agent-service.log 2>&1
    if [ $? -eq 0 ]; then
        echo "  ✓ Agent Service built"
    else
        echo "  ✗ Agent Service build FAILED - check /tmp/build-agent-service.log"
        tail -20 /tmp/build-agent-service.log
        exit 1
    fi

    # Rebuild RAG API (rag-service directory, partner-rag-api image)
    # NOTE: Always rebuild with --no-cache to ensure no stale chromadb code (we use pgvector only)
    echo "  - Building rag-api (from rag-service/)..."
    docker build --no-cache -t partner-rag-api:latest -f rag-service/Containerfile . > /tmp/build-rag-api.log 2>&1
    if [ $? -eq 0 ]; then
        echo "  ✓ RAG API built (pgvector only, no chromadb)"
    else
        echo "  ✗ RAG API build FAILED - check /tmp/build-rag-api.log"
        tail -20 /tmp/build-rag-api.log
        exit 1
    fi

    # Rebuild chat UI
    if [ -f "pf-chat-ui/Containerfile" ]; then
        echo "  - Building pf-chat-ui..."
        docker build -t partner-pf-chat-ui:latest -f pf-chat-ui/Containerfile . > /tmp/build-pf-chat-ui.log 2>&1
        if [ $? -eq 0 ]; then
            echo "  ✓ Chat UI built"
        else
            echo "  ⚠ Chat UI build failed (non-critical)"
        fi
    fi
else
    echo "  ✗ Docker not available!"
    exit 1
fi

echo "  💡 Tip: Use SKIP_BUILD=true to skip rebuilds if images are up-to-date"

# =============================================================================
# STEP 2: Infrastructure Containers
# =============================================================================
echo ""
echo "[2/8] Starting infrastructure containers..."
bash "$SEED_DIR/seed-containers.sh"

# =============================================================================
# STEP 3: Seed Keycloak
# =============================================================================
echo ""
echo "[3/8] Seeding Keycloak (users, groups, roles)..."
bash "$SEED_DIR/seed-keycloak.sh"

# =============================================================================
# STEP 4: Configure Client & Get Secret
# =============================================================================
echo ""
echo "[4/8] Configuring Keycloak client..."
CLIENT_SECRET=$(bash "$SEED_DIR/seed-client.sh" 2>&1 | tail -1)

if [ -z "$CLIENT_SECRET" ] || [ "$CLIENT_SECRET" = "null" ]; then
    echo "ERROR: Failed to get client secret"
    exit 1
fi

echo "  ✓ Client secret obtained"
export CLIENT_SECRET

# =============================================================================
# STEP 5: Export All Environment Variables
# =============================================================================
echo ""
echo "[5/8] Exporting environment variables..."
export GOOGLE_API_KEY
export DATABASE_URL
export GEMINI_MODEL
export LLM_BACKEND
export KEYCLOAK_URL
export REALM
echo "  ✓ Environment variables exported"

# =============================================================================
# STEP 6: Application Services + SPIRE Registration
# =============================================================================
echo ""
echo "[6/8] Starting application services..."
bash "$SEED_DIR/seed-services.sh"

# =============================================================================
# STEP 7: Verify SPIRE Registration
# =============================================================================
echo ""
echo "[7/8] Verifying SPIRE workload registration..."
ENTRY_COUNT=$(docker exec spire-server /opt/spire/bin/spire-server entry show 2>/dev/null | grep -c "^Entry ID" || echo "0")
if [ "$ENTRY_COUNT" -ge 2 ]; then
    echo "  ✓ SPIRE workload entries: $ENTRY_COUNT"
else
    echo "  ✗ Expected at least 2 SPIRE workload entries, found: $ENTRY_COUNT"
    echo "    Workloads cannot obtain SVIDs — chat will fail with SPIRE errors."
    echo "    Run: bash scripts/seed/seed-services.sh  to re-register."
    # Don't exit — let health checks run so the user sees which services are up
fi

# =============================================================================
# STEP 8: Health Checks
# =============================================================================
echo ""
echo "[8/8] Running health checks..."
sleep 5  # Give services time to start

# Check request-manager
if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    echo "  ✓ Request Manager healthy"
else
    echo "  ✗ Request Manager not responding"
fi

# Check agent-service
if curl -sf http://localhost:8001/health > /dev/null 2>&1; then
    echo "  ✓ Agent Service healthy"
else
    echo "  ✗ Agent Service not responding"
fi

# Check Web UI
if curl -sf http://localhost:3000 > /dev/null 2>&1; then
    echo "  ✓ Web UI healthy"
else
    echo "  ✗ Web UI not responding"
fi

# =============================================================================
# COMPLETE
# =============================================================================
echo ""
echo "════════════════════════════════════════════════════════════"
echo "✓ SETUP COMPLETE"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "System ready at:"
echo "  • Web UI:          http://localhost:3000"
echo "  • Request Manager: http://localhost:8000"
echo "  • Agent Service:   http://localhost:8001"
echo "  • RAG API:         http://localhost:8080"
echo "  • Keycloak:        http://localhost:8090"
echo "  • OPA:             http://localhost:8181"
echo ""
echo "Test users: carlos, luis, sharon, josh (password: <name>123)"
echo ""
echo "To monitor: bash scripts/monitor.sh"
echo ""
