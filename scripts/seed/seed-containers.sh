#!/bin/bash
# Start all required containers with proper SPIRE setup
# Called by: scripts/setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "════════════════════════════════════════════════════════════"
echo "STARTING INFRASTRUCTURE CONTAINERS"
echo "════════════════════════════════════════════════════════════"
echo ""

# Create network
echo "[1/7] Ensuring Docker network exists..."
if ! docker network inspect partner-agent-network >/dev/null 2>&1; then
    docker network create partner-agent-network
    echo "  ✓ Created network"
else
    echo "  - Network exists"
fi
echo ""

# PostgreSQL
echo "[2/7] Starting PostgreSQL..."
if ! docker ps --format '{{.Names}}' | grep -q "^partner-postgres-full$"; then
    docker stop partner-postgres-full 2>/dev/null || true
    docker rm partner-postgres-full 2>/dev/null || true

    docker run -d \
        --name partner-postgres-full \
        --network partner-agent-network \
        -p 5432:5432 \
        -e POSTGRES_USER=user \
        -e POSTGRES_PASSWORD=pass \
        -e POSTGRES_DB=partner_agent \
        pgvector/pgvector:pg16 > /dev/null

    echo "  ✓ PostgreSQL started"
    sleep 3

    # Schema will be created by migrations - don't create tables here
    echo "  ✓ Database ready for migrations"
else
    echo "  - PostgreSQL already running"
fi
echo ""

# Keycloak
echo "[3/7] Starting Keycloak..."
if ! docker ps --format '{{.Names}}' | grep -q "^partner-keycloak-full$"; then
    docker stop partner-keycloak-full 2>/dev/null || true
    docker rm partner-keycloak-full 2>/dev/null || true

    docker run -d \
        --name partner-keycloak-full \
        --network partner-agent-network \
        --hostname partner-keycloak-full \
        -p 8090:8090 \
        -v "$PROJECT_ROOT/keycloak/realm-base.json:/opt/keycloak/data/import/realm-base.json:ro" \
        -e KEYCLOAK_ADMIN=admin \
        -e KEYCLOAK_ADMIN_PASSWORD=admin123 \
        -e KC_HTTP_ENABLED=true \
        -e KC_HEALTH_ENABLED=true \
        -e KC_HTTP_PORT=8090 \
        -e KC_FEATURES=token-exchange,admin-fine-grained-authz \
        -e KC_HOSTNAME=partner-keycloak-full \
        -e KC_HOSTNAME_PORT=8090 \
        -e KC_HOSTNAME_STRICT=false \
        -e KC_HOSTNAME_STRICT_HTTPS=false \
        quay.io/keycloak/keycloak:latest start-dev --import-realm --features=token-exchange,admin-fine-grained-authz --http-port=8090 > /dev/null

    echo "  ✓ Keycloak started with token-exchange enabled"
    echo "  Waiting for Keycloak to be ready..."
    for i in {1..30}; do
        if curl -sf "http://localhost:8090/realms/master" > /dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    echo "  ✓ Keycloak ready"
else
    echo "  - Keycloak already running"
fi
echo ""

# OPA
echo "[4/7] Starting OPA..."
if ! docker ps --format '{{.Names}}' | grep -q "^partner-opa-full$"; then
    docker stop partner-opa-full 2>/dev/null || true
    docker rm partner-opa-full 2>/dev/null || true

    docker run -d \
        --name partner-opa-full \
        --network partner-agent-network \
        -p 8181:8181 \
        -v "$PROJECT_ROOT/policies:/policies" \
        openpolicyagent/opa:latest run --server --addr :8181 /policies > /dev/null

    echo "  ✓ OPA started"
else
    echo "  - OPA already running"
fi
echo ""

# SPIRE Server
echo "[5/7] Starting SPIRE Server..."
if ! docker ps --format '{{.Names}}' | grep -q "^spire-server$"; then
    docker stop spire-server 2>/dev/null || true
    docker rm spire-server 2>/dev/null || true

    # Create SPIRE server config
    mkdir -p "$PROJECT_ROOT/spire/server"
    cat > "$PROJECT_ROOT/spire/server/server.conf" <<'EOF'
server {
    bind_address = "0.0.0.0"
    bind_port = "8081"
    trust_domain = "partner.example.com"
    data_dir = "/opt/spire/data/server"
    log_level = "INFO"
    ca_ttl = "168h"
    default_x509_svid_ttl = "48h"
}

plugins {
    DataStore "sql" {
        plugin_data {
            database_type = "sqlite3"
            connection_string = "/opt/spire/data/server/datastore.sqlite3"
        }
    }

    NodeAttestor "join_token" {
        plugin_data {}
    }

    KeyManager "disk" {
        plugin_data {
            keys_path = "/opt/spire/data/server/keys.json"
        }
    }
}
EOF

    docker run -d \
        --name spire-server \
        --network partner-agent-network \
        -p 8081:8081 \
        -v "$PROJECT_ROOT/spire/server:/opt/spire/conf" \
        ghcr.io/spiffe/spire-server:1.8.0 \
        -config /opt/spire/conf/server.conf > /dev/null

    echo "  ✓ SPIRE Server started"
    echo "  Waiting for SPIRE Server to be ready..."
    sleep 5
    echo "  ✓ SPIRE Server ready"
else
    echo "  - SPIRE Server already running"
fi
echo ""

# SPIRE Agent (separate container - proper architecture!)
echo "[6/7] Starting SPIRE Agent..."
if ! docker ps --format '{{.Names}}' | grep -q "^partner-spire-agent$"; then
    docker stop partner-spire-agent 2>/dev/null || true
    docker rm partner-spire-agent 2>/dev/null || true

    # Generate join token for agent
    AGENT_TOKEN=$(docker exec spire-server \
        /opt/spire/bin/spire-server token generate \
        -spiffeID spiffe://partner.example.com/agent/main 2>&1 | \
        grep "Token:" | awk '{print $2}')

    # SPIRE agent uses existing config file (spire/agent/agent.conf)
    # DO NOT regenerate - it has Docker workload attestor configured
    if [ ! -f "$PROJECT_ROOT/spire/agent/agent.conf" ]; then
        echo "  ✗ ERROR: spire/agent/agent.conf not found!" >&2
        echo "  This file should exist in the repository with Docker attestor configured." >&2
        exit 1
    fi

    # Start SPIRE Agent with shared socket volume AND Docker socket
    # CRITICAL: --pid=host required for Docker attestor to identify workloads across containers
    docker run -d \
        --name partner-spire-agent \
        --pid=host \
        --network partner-agent-network \
        -v "$PROJECT_ROOT/spire/agent:/opt/spire/conf" \
        -v spire-socket:/run/spire/sockets \
        -v /var/run/docker.sock:/var/run/docker.sock:ro \
        -e JOIN_TOKEN="$AGENT_TOKEN" \
        ghcr.io/spiffe/spire-agent:1.9.0 \
        -config /opt/spire/conf/agent.conf \
        -joinToken "$AGENT_TOKEN" > /dev/null

    echo "  ✓ SPIRE Agent started"
    echo "  Waiting for agent socket..."
    for i in {1..30}; do
        if docker inspect partner-spire-agent --format '{{.State.Running}}' 2>/dev/null | grep -q true && \
           docker volume inspect spire-socket >/dev/null 2>&1; then
            echo "  ✓ SPIRE Agent socket ready"
            break
        fi
        if [ $i -eq 30 ]; then
            echo "  ✗ SPIRE Agent socket timeout"
        fi
        sleep 1
    done
else
    echo "  - SPIRE Agent already running"
fi
echo ""

# Agent Service and Request Manager deferred to seed-services.sh
echo "[7/7] Application services setup..."
echo "  - Deferred to seed-services.sh"
echo ""

echo "════════════════════════════════════════════════════════════"
echo "✓ CORE INFRASTRUCTURE RUNNING"
echo "════════════════════════════════════════════════════════════"
echo ""
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep -E 'NAMES|partner'
echo ""
