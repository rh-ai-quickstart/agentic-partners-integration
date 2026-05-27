# ARO Support Agent — Azure MCP Server Integration

> **This branch** extends the [Partner Agent Integration Framework](https://github.com/rh-ai-quickstart/agentic-partners-integration) with an ARO Support Agent that uses [Microsoft's Azure MCP Server](https://github.com/microsoft/mcp/tree/main/servers/Azure.Mcp.Server) for live Azure infrastructure troubleshooting via tool calling.
>
> For the core framework (routing, security, RAG, A2A protocol), see the [`main` branch README](https://github.com/rh-ai-quickstart/agentic-partners-integration/tree/main).

## What This Branch Adds

Unlike the in-process Software and Network agents that rely on RAG over a local knowledge base, the ARO agent connects to a live Azure MCP server exposing 40+ tools across Azure services (AKS, Storage, Cosmos DB, Key Vault, Monitor, etc.). The LLM dynamically discovers available tools, decides which to invoke based on the user's question, and executes them via the MCP protocol to inspect real infrastructure state before generating a grounded response.

```mermaid
flowchart LR
    User["User asks:\n'My pods are OOMKilled'"]
    Agent["ARO Agent\nconnects to Azure\nvia MCP"]
    Investigate["AI investigates:\n1. Check cluster metrics\n2. Read memory limits\n3. Correlate peak traffic"]
    Answer["Specific answer:\n'Pods use 240Mi of 256Mi limit.\nSpikes at 14:00 UTC.\nIncrease to 512Mi.'"]

    User --> Agent --> Investigate --> Answer

    style User fill:#e3f2fd,stroke:#1565c0
    style Agent fill:#e8eaf6,stroke:#283593
    style Investigate fill:#fff3e0,stroke:#e65100
    style Answer fill:#e8f5e9,stroke:#2e7d32
```

The answer references **real data** the agent just pulled from Azure — no hallucination, no generic advice. The user can verify every claim by checking the same metrics themselves.

```mermaid
flowchart LR
    User["User asks:\n'My pods are OOMKilled'"]
    RM[Request Manager]
    ARO[ARO Agent]
    LLM[LLM]
    MCP[Azure MCP Server]
    Azure[Azure Services<br/>AKS, Storage,<br/>Cosmos DB, ...]

    User --> RM -->|A2A HTTP| ARO
    ARO -->|tool definitions + calls| LLM
    ARO -->|MCP protocol| MCP --> Azure

    style ARO fill:#e8eaf6,stroke:#283593
    style MCP fill:#e3f2fd,stroke:#1565c0
```

## How It Works

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

## Where It Fits in the Architecture

The ARO agent plugs into the existing multi-agent ecosystem through the same contract used by knowledge-based agents. From the user's perspective, nothing changes — they ask a question and get an answer. Behind the scenes, different agents use fundamentally different approaches to find that answer:

```mermaid
flowchart LR
    users["Users"]

    subgraph frontend[" Web Frontend "]
        ui["Chat UI"]
    end

    subgraph orchestrator[" Orchestrator "]
        adk["Request Manager\n+ Policy Engine\n+ Identity Provider"]
    end

    subgraph rag_agents[" Knowledge-based Agents "]
        sw["Software\nSupport"]
        nw["Network\nSupport"]
    end

    subgraph mcp_agents[" Live Infrastructure Agents "]
        k8s["Kubernetes\nSupport"]
        aro["ARO\nSupport"]
    end

    kb["Knowledge\nBase"]
    cloud["Live Cloud\nInfrastructure"]

    users --> ui --> adk
    adk --> sw & nw
    adk --> k8s & aro
    sw & nw -->|search tickets| kb
    k8s & aro -->|inspect systems| cloud

    style frontend fill:#e3f2fd,stroke:#1565c0
    style orchestrator fill:#fff3e0,stroke:#e65100
    style rag_agents fill:#e8f5e9,stroke:#2e7d32
    style mcp_agents fill:#e8eaf6,stroke:#283593
    style sw fill:#e8f5e9,stroke:#2e7d32
    style nw fill:#e8f5e9,stroke:#2e7d32
    style k8s fill:#e8eaf6,stroke:#283593
    style aro fill:#e8eaf6,stroke:#283593
```

The green agents (Software, Network) use **RAG** — they search historical tickets to find documented solutions. The blue agents (Kubernetes, ARO) use **MCP** — they connect to live systems to investigate current state. This coexistence is the key insight: different problems need different approaches, but users don't need to know which approach is being used.

```mermaid
flowchart LR
    users["Users"]

    subgraph frontend[" RH Web Frontend "]
        ui["PatternFly\nChat UI"]
    end

    subgraph orchestrator[" Orchestrator Layer "]
        adk["Google ADK\n(Self-hosted)\n\nRequest Manager\n+ OPA + Keycloak"]
    end

    llm_main["LLM\n(Gemini 2.5 Flash)"]

    subgraph agents[" Domain-based Agents "]
        sw["Software\nSupport Agent"]
        nw["Network\nSupport Agent"]
        k8s["Kubernetes\nSupport Agent"]
        aro["ARO Support\nAgent"]
    end

    llm_sw["LLM"]
    llm_nw["LLM"]
    llm_k8s["LLM"]
    llm_aro["LLM"]

    users --> ui
    ui --> adk
    adk --> llm_main

    adk -->|A2A| sw
    adk -->|A2A| nw
    adk -->|A2A| k8s
    adk -->|A2A| aro

    sw --- llm_sw
    nw --- llm_nw
    k8s --- llm_k8s
    aro --- llm_aro

    style frontend fill:#e3f2fd,stroke:#1565c0
    style orchestrator fill:#fff3e0,stroke:#e65100
    style agents fill:#e8f5e9,stroke:#2e7d32
    style sw fill:#e8f5e9,stroke:#2e7d32
    style nw fill:#e8f5e9,stroke:#2e7d32
    style k8s fill:#e8eaf6,stroke:#283593
    style aro fill:#e8eaf6,stroke:#283593
```

The ARO agent (shown in blue) runs as a separate container with its own LLM connection. It communicates with the orchestrator solely through the A2A HTTP contract — no shared code, no shared state.

## Key Characteristics

- **Fully independent black box** — uses the OpenAI SDK directly, runs as its own container, and communicates with the orchestrator solely through `POST /api/v1/agents/aro-support/invoke`.
- **MCP tool-calling loop** — fetches tool definitions from the Azure MCP server at runtime, passes them to the LLM, executes any requested tool calls, and feeds results back until the LLM produces a final answer.
- **Configurable tool filter** — limits which of the 110 Azure MCP tools the LLM sees (e.g., only `search`, `storage`, `container`, `cosmos`, `monitor`) to keep context windows manageable.
- **Multiple deployment options** — the Azure MCP server can run via npm locally, as a container, or deployed from the Red Hat AI on OpenShift catalog.
- **Graceful degradation** — if no MCP server is configured, the agent falls back to answering from LLM knowledge alone.

## Quick Start

### Prerequisites

- The core framework running from `main` (see [Getting Started](docs/getting-started.md))
- Python 3.12+
- A Google API key for Gemini (default) — or any OpenAI-compatible API
- **Optional:** Azure MCP server + Azure credentials (for live Azure tool access)

### 1. Start the core framework

```bash
git clone https://github.com/rh-ai-quickstart/agentic-partners-integration
cd agentic-partners-integration
git checkout aro
export GOOGLE_API_KEY=your-key-here   # or add to .env
make setup                            # builds, starts, and configures everything
```

### 2. Run the ARO agent without MCP (basic LLM mode)

```bash
cd aro-partner-agent
uv sync
GOOGLE_API_KEY=AIza... uv run python -m aro_agent.main
```

The agent starts on port 8080 and answers Azure/ARO questions using LLM knowledge only. No Azure credentials needed.

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

**Option A — npm (local development):**

```bash
az login
npx -y @azure/mcp@latest server start --transport http
# Starts on http://localhost:5008/mcp
```

**Option B — container:**

```bash
docker run -d \
  --name azure-mcp-server \
  --network partner-agent-network \
  -e AZURE_TENANT_ID=<TENANT_ID> \
  -e AZURE_CLIENT_ID=<CLIENT_ID> \
  -e AZURE_CLIENT_SECRET=<CLIENT_SECRET> \
  -e AZURE_SUBSCRIPTION_ID=<SUBSCRIPTION_ID> \
  -e ASPNETCORE_URLS=http://+:8080 \
  -e DOTNET_BUNDLE_EXTRACT_BASE_DIR=/tmp/.net \
  -e HOME=/tmp \
  -e ALLOW_INSECURE_EXTERNAL_BINDING=true \
  -p 5008:8080 \
  quay.io/rhoai-partner-mcp/ubi10-ms-azure-mcp-server:1774539732-dotnet-builder \
  --transport http
```

**Option C — RHAOI catalog on OpenShift/ARO:**

Deploy the Azure MCP server from the Red Hat AI on OpenShift MCP catalog. See [`aro-partner-agent/README.md`](aro-partner-agent/README.md) for full deployment instructions including secret creation.

Then point the ARO agent at the MCP server:

```bash
GOOGLE_API_KEY=AIza... \
MCP_SERVER_URL=http://localhost:5008/mcp \
uv run python -m aro_agent.main
```

## What Changed from `main`

| Area | Change |
|------|--------|
| `aro-partner-agent/` | New self-contained Python agent with MCP client, OpenAI SDK, and full test suite |
| `azure-mcp-server/` | Container build and MCP proxy utilities for the Azure MCP server |
| `docker-compose.yaml` | Added ARO agent and Azure MCP server services |
| `agent-service/config/` | ARO support agent YAML registration |
| `keycloak/realm-partner.json` | Added `azure` department for ARO agent authorization |
| `policies/` | Updated OPA rules for ARO agent delegation |

## Beyond Azure: An Ecosystem Strategy

The MCP integration is not Azure-specific. The same pattern works for any external service that has an MCP server. Each new MCP server that Microsoft, AWS, Google, or any vendor publishes instantly becomes a potential new agent capability — with no framework changes, no new integrations, no cross-team coordination:

```mermaid
flowchart TB
    subgraph framework["Partner Agent Framework"]
        RM["Orchestrator + Policy Engine"]
        RM --> SW["Software Agent\nsearches tickets"]
        RM --> NW["Network Agent\nsearches tickets"]
        RM --> ARO_A["ARO Agent\ninspects Azure"]
        RM --> AWS_A["AWS Agent\ninspects AWS"]
        RM --> GH_A["GitHub Agent\nsearches repos"]
        RM --> CUSTOM["Custom Agent\nany approach"]
    end

    ARO_A --> AZURE["Azure"]
    AWS_A --> AWS["AWS"]
    GH_A --> GH["GitHub"]
    CUSTOM --> INT["Internal Systems"]

    style framework fill:#f5f5f5,stroke:#424242
    style SW fill:#e8f5e9,stroke:#2e7d32
    style NW fill:#e8f5e9,stroke:#2e7d32
    style ARO_A fill:#e8eaf6,stroke:#283593
    style AWS_A fill:#e8eaf6,stroke:#283593
    style GH_A fill:#e8eaf6,stroke:#283593
    style CUSTOM fill:#fff3e0,stroke:#e65100
```

## Detailed Documentation

For the full ARO agent documentation including Azure credential setup, tool filtering, all deployment options, and testing — see [`aro-partner-agent/README.md`](aro-partner-agent/README.md).

For the core framework documentation (architecture, security, RAG, A2A protocol, configuration) — see the [`main` branch](https://github.com/rh-ai-quickstart/agentic-partners-integration/tree/main).
