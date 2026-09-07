#!/bin/bash
# Stop E2E Deployment
set -e

echo "=== Stopping E2E Deployment ==="

# Stop and remove containers
docker stop partner-pf-chat-ui-e2e 2>/dev/null || true
docker stop partner-request-manager-e2e 2>/dev/null || true
docker stop partner-agent-service-e2e 2>/dev/null || true
docker stop partner-rag-api-e2e 2>/dev/null || true
docker stop partner-opa-e2e 2>/dev/null || true
docker stop partner-keycloak-e2e 2>/dev/null || true
docker stop partner-postgres-e2e 2>/dev/null || true

docker rm partner-pf-chat-ui-e2e 2>/dev/null || true
docker rm partner-request-manager-e2e 2>/dev/null || true
docker rm partner-agent-service-e2e 2>/dev/null || true
docker rm partner-rag-api-e2e 2>/dev/null || true
docker rm partner-opa-e2e 2>/dev/null || true
docker rm partner-keycloak-e2e 2>/dev/null || true
docker rm partner-postgres-e2e 2>/dev/null || true

# Remove network
docker network rm partner-agent-network 2>/dev/null || true

echo "✓ All containers stopped and removed"
echo "✓ Network removed"
