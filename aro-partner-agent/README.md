# ARO Partner Agent

Standalone Azure Red Hat OpenShift (ARO) support agent that connects to
Azure services through the [Azure MCP Server](https://github.com/microsoft/mcp/tree/main/servers/Azure.Mcp.Server).

This agent is an **independent black box** — it uses the OpenAI SDK
directly and communicates with the rest of the quickstart only through
the A2A HTTP contract (`POST /api/v1/agents/aro-support/invoke`).

## How it works

```mermaid
flowchart LR
    User -->|question| RM[Request Manager]
    RM -->|A2A invoke| ARO[ARO Agent]
    ARO -->|tool definitions| LLM[OpenAI / LLM]
    LLM -->|tool calls| ARO
    ARO -->|MCP call_tool| MCP[Azure MCP Server<br/>40+ tools]
    MCP -->|results| ARO
    ARO -->|tool results| LLM
    LLM -->|final answer| ARO
    ARO -->|response| RM
    MCP --> Azure[Azure Services<br/>Storage, AKS,<br/>Cosmos DB, ...]
```

```mermaid
sequenceDiagram
    participant U as User
    participant RM as Request Manager
    participant A as ARO Agent
    participant LLM as OpenAI / LLM
    participant MCP as Azure MCP Server
    participant AZ as Azure Services

    U->>RM: "My pods are OOMKilled"
    RM->>A: A2A invoke (azure dept)
    A->>MCP: list_tools()
    MCP-->>A: tool definitions
    A->>LLM: question + tools
    LLM-->>A: call search("OOMKilled")
    A->>MCP: call_tool("search", ...)
    MCP->>AZ: Azure AI Search query
    AZ-->>MCP: search results
    MCP-->>A: tool result
    A->>LLM: question + tool result
    LLM-->>A: final answer
    A-->>RM: response
    RM-->>U: "Based on the search results..."
```

1. The agent receives a question via A2A invoke
2. It connects to the Azure MCP server and fetches available tool definitions
3. It sends the question + tool definitions to the LLM
4. The LLM decides whether to call tools (search an index, list AKS clusters, etc.)
5. If the LLM requests tool calls, the agent executes them via MCP and feeds results back
6. The loop repeats until the LLM produces a final text answer
7. If no MCP server is configured, the agent answers using LLM knowledge only

## Prerequisites

- Python 3.12+
- An LLM API key — any OpenAI-compatible API (see [Configuration](../docs/configuration.md) for supported backends)
- **Optional:** Azure MCP server + Azure credentials (for live Azure tool access)

## Getting your API keys

### LLM API key

**Recommended (new):**

```bash
export AI_API_KEY=your-key-here
```

**Alternative (legacy, still supported):**

```bash
export GOOGLE_API_KEY=your-key-here  # Deprecated
# or
export OPENAI_API_KEY=your-key-here  # Deprecated
```

This is the same API key used by the rest of the quickstart (agent-service, request-manager, rag-api). The new `AI_API_KEY` variable works with any LLM backend. See [Configuration](../docs/configuration.md) for the migration guide.

### Azure credentials (for the MCP server)

The Azure MCP server uses Azure AD JWT Bearer authentication. You need
an Azure AD app registration with a service principal:

```bash
# Create a service principal
az ad sp create-for-rbac --name "mcp-server-sp" \
  --scopes /subscriptions/$(az account show --query id -o tsv)

# Output:
# {
#   "appId": "...",     ← AZURE_CLIENT_ID
#   "password": "...",  ← AZURE_CLIENT_SECRET
#   "tenant": "..."     ← AZURE_TENANT_ID
# }

# Add an application ID URI (required for incoming JWT auth)
az ad app update --id <appId> --identifier-uris "api://<appId>"
```

Then configure the credentials in `azure-mcp-server/.env`:

```bash
cp ../azure-mcp-server/.env.example ../azure-mcp-server/.env
# Edit with your values:
#   AZURE_TENANT_ID=<tenant>
#   AZURE_CLIENT_ID=<appId>
#   AZURE_CLIENT_SECRET=<password>
#   AZURE_SUBSCRIPTION_ID=<subscription-id>
```

The same credentials are used for:
- **MCP server incoming auth** — `AzureAd__ClientId`/`AzureAd__TenantId` validate JWT tokens
- **MCP server outgoing auth** — `AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET` authenticate to Azure APIs
- **ARO agent token acquisition** — `MCP_AZURE_*` vars let the agent acquire Bearer tokens via `client_credentials` grant

> **Note:** The MCP server runs with `--read-only` to prevent destructive operations.

## Quick start

### 1. Install dependencies

```bash
cd aro-partner-agent
uv sync
```

### 2. Run without MCP (basic LLM mode)

```bash
GOOGLE_API_KEY=your-key-here uv run python -m aro_agent.main
```

The agent starts on port 8080 and answers Azure/ARO questions using LLM
knowledge only. No Azure credentials needed.

```bash
curl -X POST http://localhost:8080/api/v1/agents/aro-support/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-1",
    "user_id": "carlos@example.com",
    "message": "My pods on ARO keep getting OOMKilled"
  }'
```

### 3. Run with Azure MCP Server (live Azure tools)

Ensure you've configured `azure-mcp-server/.env` (see [Azure credentials](#azure-credentials-for-the-mcp-server) above).

**Start the MCP server and agent together:**

```bash
# From the project root
make setup
```

Or run them individually:

```bash
# Start the MCP server (Red Hat catalog image, pinned to v2.0.0-beta.28)
docker run -d \
  --name azure-mcp-server \
  --network partner-agent-network \
  --env-file ../azure-mcp-server/.env \
  -e HOME=/tmp \
  -e DOTNET_ROOT=/usr/lib64/dotnet \
  -e DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp/.net \
  -e ASPNETCORE_URLS=http://+:8080 \
  -e AzureAd__ClientId="${AZURE_CLIENT_ID}" \
  -e AzureAd__TenantId="${AZURE_TENANT_ID}" \
  -e "AzureAd__Instance=https://login.microsoftonline.com/" \
  -p 5008:8080 \
  --entrypoint="" \
  quay.io/rhoai-partner-mcp/ubi10-ms-azure-mcp-server:1774539732-dotnet-builder \
  /mcp-server/azmcp server start --transport http --read-only \
    --outgoing-auth-strategy UseHostingEnvironmentIdentity

# Start the ARO agent (acquires Bearer tokens automatically)
AI_API_KEY=your-key-here \
MCP_SERVER_URL=http://localhost:5008/ \
MCP_AZURE_TENANT_ID="${AZURE_TENANT_ID}" \
MCP_AZURE_CLIENT_ID="${AZURE_CLIENT_ID}" \
MCP_AZURE_CLIENT_SECRET="${AZURE_CLIENT_SECRET}" \
uv run python -m aro_agent.main
```

Now the agent has access to 40+ Azure tools. Try:

```bash
# List AKS clusters
curl -s -X POST http://localhost:8080/api/v1/agents/aro-support/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-2",
    "user_id": "carlos@example.com",
    "message": "List my AKS clusters and their node counts"
  }' | jq .content

# Query Cosmos DB
curl -s -X POST http://localhost:8080/api/v1/agents/aro-support/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-3",
    "user_id": "carlos@example.com",
    "message": "Show me the databases in my Cosmos DB account"
  }' | jq .content

# Search Azure AI Search
curl -s -X POST http://localhost:8080/api/v1/agents/aro-support/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "test-4",
    "user_id": "carlos@example.com",
    "message": "Search our knowledge base for OOMKilled runbooks"
  }' | jq .content
```

### 4. Run with Ollama (free, no API key)

```bash
# Install and start Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3

# Run the ARO agent against local Ollama
OPENAI_API_KEY=unused \
OPENAI_BASE_URL=http://localhost:11434/v1 \
OPENAI_MODEL=llama3 \
uv run python -m aro_agent.main
```

## Running with the full quickstart

The ARO agent integrates with the quickstart via `make setup` (Docker
for development) or Helm (production). Users with the **azure**
department are routed to this agent automatically.

```bash
# From the project root
make build
make setup
```

The agent runs on port **8004** and is registered in the agent-service
as `aro-support` with `departments: ["azure"]`.

Test users with azure access:

| User | Password | Departments |
|------|----------|-------------|
| carlos@example.com | carlos123 | software, kubernetes, **azure** |
| sharon@example.com | sharon123 | software, network, kubernetes, **azure** (admin) |

## Configuration

The agent is configured via `config/agents/aro-support-agent.yaml`:

```yaml
name: "aro-support"
departments: ["azure"]
llm_model: "gemini-2.5-flash"

mcp_servers:
  - name: azure
    url: "http://azure-mcp-server:8080/"
    transport: "http"          # "http" (StreamableHTTP) or "sse"
    tool_filter:               # only expose tools containing these keywords
      - search
      - storage
      - container
      - cosmos
      - sql
      - keyvault
      - monitor
```

### Environment variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AI_API_KEY` | Yes | — | LLM API key (works with any provider) |
| `OPENAI_BASE_URL` | No | — | Override for Azure OpenAI, Ollama, etc. |
| `OPENAI_MODEL` | No | — | LLM model to use |
| `MCP_SERVER_URL` | No | from YAML config | MCP server endpoint (overrides YAML) |
| `MCP_TRANSPORT` | No | `http` | MCP transport: `http` or `sse` |
| `MCP_AZURE_TENANT_ID` | No | — | Azure AD tenant for MCP server auth |
| `MCP_AZURE_CLIENT_ID` | No | — | Azure AD client ID for MCP server auth |
| `MCP_AZURE_CLIENT_SECRET` | No | — | Azure AD client secret for MCP server auth |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `PORT` | No | `8080` | Server port |

When `MCP_AZURE_*` variables are set, the agent acquires Bearer tokens via the OAuth 2.0 `client_credentials` grant and includes them in MCP requests. Tokens are cached and refreshed automatically.

### MCP tool filter

The `tool_filter` in the config limits which tools the LLM sees. Each
entry is a **keyword** — any tool whose name contains the keyword is
included. For example, `storage` matches `get_azure_storage_details`,
`create_azure_storage_account`, etc. The Azure MCP server exposes
110 tools across 40+ Azure services:

| Filter keyword | Example tools matched |
|---------------|----------------------|
| `search` | `search_azure_ai_index`, `get_azure_ai_search_details` |
| `storage` | `get_azure_storage_details`, `create_azure_storage_account` |
| `container` | `get_azure_container_details`, `create_azure_container_app` |
| `cosmos` | `get_azure_cosmos_details`, `create_azure_cosmos_database` |
| `sql` | `get_azure_sql_details`, `create_azure_sql_database` |
| `keyvault` | `get_azure_keyvault_details`, `create_azure_keyvault_secret` |
| `monitor` | `get_azure_monitor_details`, `get_azure_monitor_metrics` |
| `compute` | `get_azure_compute_details`, `create_azure_vm` |
| `postgres` | `get_azure_postgres_details` |
| `redis` | `get_azure_redis_details`, `create_azure_redis_cache` |

Remove `tool_filter` entirely to expose all 110 tools to the LLM.

### MCP transports

| Transport | Config value | URL pattern | Use case |
|-----------|-------------|-------------|----------|
| StreamableHTTP | `http` (default) | `/` | Azure MCP server, production deployments |
| Server-Sent Events | `sse` | `/sse` | Legacy MCP servers |

## API

### `POST /api/v1/agents/aro-support/invoke`

```json
{
  "session_id": "abc-123",
  "user_id": "carlos@example.com",
  "message": "My pods on ARO keep getting OOMKilled",
  "transfer_context": {
    "conversation_history": [
      {"role": "user", "content": "previous message"},
      {"role": "assistant", "content": "previous response"}
    ]
  }
}
```

Response:

```json
{
  "content": "Based on the AKS cluster data, your pods are hitting ...",
  "agent_id": "aro-support",
  "session_id": "abc-123",
  "metadata": {
    "mcp_enabled": true
  }
}
```

### `GET /health`

```json
{
  "status": "healthy",
  "service": "aro-partner-agent",
  "version": "0.1.0",
  "mcp_configured": true,
  "timestamp": "2026-04-20T12:00:00Z"
}
```

## Tests

```bash
uv run pytest tests/ -v
```

29 tests covering the agent, MCP client, and FastAPI endpoints.
All tests are fully mocked — no API keys or MCP server needed.

## Architecture

This agent is a **fully independent black box**. It shares no code
with the other agents in the quickstart — only the A2A HTTP contract.
A partner could rewrite this agent in any language or framework and
the quickstart would work identically, as long as the invoke endpoint
returns the same JSON schema.

```mermaid
graph TD
    subgraph aro-partner-agent
        subgraph config
            YAML[agents/aro-support-agent.yaml<br/>agent config + MCP settings]
        end
        subgraph src/aro_agent
            AGENT[agent.py<br/>AROAgent: LLM + MCP tool-calling loop]
            MCP[mcp_client.py<br/>MCPClient: http/sse transports]
            MAIN[main.py<br/>FastAPI: /health + /invoke]
            SCHEMAS[schemas.py<br/>Pydantic request/response]
        end
        subgraph tests
            TA[test_agent.py — 11 tests]
            TM[test_mcp_client.py — 13 tests]
            TN[test_main.py — 5 tests]
        end
        CF[Containerfile — UBI9 multi-stage build]
        PP[pyproject.toml]
    end

    AGENT --> MCP
    MAIN --> AGENT
    MAIN --> SCHEMAS
```
