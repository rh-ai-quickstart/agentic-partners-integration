#!/bin/bash
# E2E Deployment Script using Pre-Published Images
set -e

echo "=== Starting E2E Deployment with Pre-Published Images ==="
echo ""

# Load .env file
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
    echo "✓ Loaded .env file"
else
    echo "✗ .env file not found!"
    exit 1
fi

# Create network
echo ""
echo "Creating Docker network..."
docker network create partner-agent-network 2>/dev/null || echo "Network already exists"

# Start PostgreSQL
echo ""
echo "Starting PostgreSQL..."
docker run -d --name partner-postgres-e2e \
    --network partner-agent-network \
    -e POSTGRES_USER=user \
    -e POSTGRES_PASSWORD=pass \
    -e POSTGRES_DB=partner_agent \
    -p 5432:5432 \
    pgvector/pgvector:pg16

# Wait for PostgreSQL
echo "Waiting for PostgreSQL to be ready..."
sleep 10
until docker exec partner-postgres-e2e pg_isready -U user -d partner_agent 2>/dev/null; do
    echo "  Waiting..."
    sleep 2
done
echo "✓ PostgreSQL is ready"

# Start Keycloak
echo ""
echo "Starting Keycloak..."
docker run -d --name partner-keycloak-e2e \
    --network partner-agent-network \
    -e KC_BOOTSTRAP_ADMIN_USERNAME=admin \
    -e KC_BOOTSTRAP_ADMIN_PASSWORD=admin123 \
    -e KC_HTTP_ENABLED=true \
    -e KC_HEALTH_ENABLED=true \
    -v $(pwd)/keycloak/realm-partner.json:/opt/keycloak/data/import/realm-partner.json:ro \
    -p 8090:8080 \
    quay.io/keycloak/keycloak:26.5 start-dev --import-realm

echo "Waiting for Keycloak (60s)..."
sleep 60

# Start OPA
echo ""
echo "Starting OPA..."
docker run -d --name partner-opa-e2e \
    --network partner-agent-network \
    -v $(pwd)/policies:/policies:ro \
    -p 8181:8181 \
    openpolicyagent/opa:latest run --server --addr :8181 /policies

sleep 5
echo "✓ OPA started"

# Start RAG API
echo ""
echo "Starting RAG API..."
docker run -d --name partner-rag-api-e2e \
    --network partner-agent-network \
    -e GOOGLE_API_KEY=${GOOGLE_API_KEY} \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-e2e:5432/partner_agent \
    -e EMBEDDING_MODEL=models/gemini-embedding-001 \
    -e LLM_MODEL=gemini-2.5-flash \
    -p 8080:8080 \
    ghcr.io/rh-ai-quickstart/partner-rag-api:latest

sleep 10
echo "✓ RAG API started"

# Run Database Migrations
echo ""
echo "Running database migrations..."
docker run --rm --name partner-db-migrate \
    --network partner-agent-network \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-e2e:5432/partner_agent \
    -w /app \
    ghcr.io/rh-ai-quickstart/partner-request-manager:latest \
    bash -c "cd /app/shared-models && python3 -m alembic upgrade head"

echo "✓ Migrations complete"

# Ingest RAG Data
echo ""
echo "Ingesting RAG knowledge base..."
docker run --rm --name partner-ingest-e2e \
    --network partner-agent-network \
    -v $(pwd)/data:/app/data:ro \
    -v $(pwd)/rag-service:/app/rag-service:ro \
    -e GOOGLE_API_KEY=${GOOGLE_API_KEY} \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-e2e:5432/partner_agent \
    -e EMBEDDING_MODEL=models/gemini-embedding-001 \
    -w /app \
    registry.access.redhat.com/ubi9/python-312:latest \
    bash -c "pip3 install -q pgvector psycopg[binary] sqlalchemy[asyncio] asyncpg google-genai numpy structlog && python3 rag-service/ingest_knowledge.py"

echo "✓ Knowledge base ingested"

# Start Agent Service
echo ""
echo "Starting Agent Service..."
docker run -d --name partner-agent-service-e2e \
    --network partner-agent-network \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-e2e:5432/partner_agent \
    -e LLM_BACKEND=gemini \
    -e GOOGLE_API_KEY=${GOOGLE_API_KEY} \
    -e GEMINI_MODEL=gemini-2.5-flash \
    -e LOG_LEVEL=INFO \
    -e RAG_API_ENDPOINT=http://partner-rag-api-e2e:8080/answer \
    -e MOCK_SPIFFE=true \
    -e SPIFFE_TRUST_DOMAIN=partner.example.com \
    -e OPA_URL=http://partner-opa-e2e:8181 \
    -p 8001:8080 \
    ghcr.io/rh-ai-quickstart/partner-agent-service:latest

sleep 10
echo "✓ Agent Service started"

# Start Request Manager
echo ""
echo "Starting Request Manager..."
docker run -d --name partner-request-manager-e2e \
    --network partner-agent-network \
    -e DATABASE_URL=postgresql+asyncpg://user:pass@partner-postgres-e2e:5432/partner_agent \
    -e LLM_BACKEND=gemini \
    -e GOOGLE_API_KEY=${GOOGLE_API_KEY} \
    -e GEMINI_MODEL=gemini-2.5-flash \
    -e AGENT_SERVICE_URL=http://partner-agent-service-e2e:8080 \
    -e AGENT_TIMEOUT=120 \
    -e LOG_LEVEL=INFO \
    -e STRUCTURED_CONTEXT_ENABLED=true \
    -e MOCK_SPIFFE=true \
    -e SPIFFE_TRUST_DOMAIN=partner.example.com \
    -e OPA_URL=http://partner-opa-e2e:8181 \
    -e KEYCLOAK_URL=http://partner-keycloak-e2e:8080 \
    -e KEYCLOAK_REALM=partner-agent \
    -e KEYCLOAK_CLIENT_ID=partner-agent-ui \
    -p 8000:8080 \
    ghcr.io/rh-ai-quickstart/partner-request-manager:latest

sleep 10
echo "✓ Request Manager started"

# Start PF Chat UI
echo ""
echo "Starting Web UI..."
docker run -d --name partner-pf-chat-ui-e2e \
    --network partner-agent-network \
    -p 3000:8080 \
    ghcr.io/rh-ai-quickstart/partner-pf-chat-ui:latest

sleep 5
echo "✓ Web UI started"

echo ""
echo "=== Deployment Complete! ==="
echo ""
echo "Services:"
echo "  Web UI:          http://localhost:3000"
echo "  Request Manager: http://localhost:8000"
echo "  Agent Service:   http://localhost:8001"
echo "  RAG API:         http://localhost:8080"
echo "  Keycloak:        http://localhost:8090"
echo "  OPA:             http://localhost:8181"
echo ""
echo "Test Credentials:"
echo "  carlos@example.com / carlos123  (software + kubernetes access)"
echo "  luis@example.com / luis123      (network access only)"
echo "  sharon@example.com / sharon123  (admin - all access)"
echo ""
echo "Try a query:"
echo "  'My app crashes with error 500'"
echo ""
echo "View logs:"
echo "  docker logs -f partner-request-manager-e2e"
echo ""
echo "Stop everything:"
echo "  ./stop-e2e.sh"
