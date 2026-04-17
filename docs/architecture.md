# Architecture Overview

## System Diagram

```mermaid
flowchart TB
    subgraph ui["Web UI · port 3000"]
        nginx["PatternFly Chat Interface\nnginx reverse proxy → :8080"]
    end

    subgraph rm["Request Manager · port 8000"]
        identity["Identity Middleware\nSPIFFE ID extraction (X-SPIFFE-ID mock)"]
        adk["adk_endpoints.py\n/adk/chat · /adk/audit"]
        strategy["communication_strategy.py\ninvoke routing · detect ROUTE:\nquery OPA · invoke specialist · write audit"]
        identity --> adk --> strategy
    end

    subgraph as["Agent Service · port 8001"]
        invoke["/invoke endpoint (main.py)"]
        routing["routing-agent\nBuild system prompt with departments\nLLM classifies intent → ROUTE:agent"]
        specialist["specialist agent\nQuery RAG API · build prompt with RAG context\nLLM generates grounded response"]
        llm["LLM Client Factory\nGeminiClient (default) · OpenAIClient · OllamaClient"]
        invoke --> routing & specialist
        routing --> llm
        specialist --> llm
    end

    subgraph rag["RAG API · port 8003"]
        ragapi["Embed query → Search pgvector → Return top matches"]
    end

    subgraph db["PostgreSQL + pgvector · port 5433"]
        users["users\nemail · spiffe_id · role · departments[]"]
        sessions["request_sessions\nsession_id · conversation_context{}"]
        logs["request_logs\nrequest_id · agent_id · response · timing_ms"]
    end

    gemini["Google Gemini API\ngemini-2.5-flash"]
    opa["OPA · port 8181"]
    keycloak["Keycloak · port 8090"]

    nginx -->|"POST /adk/chat\nGET /adk/audit"| adk
    strategy -->|"A2A: POST /api/v1/agents/{name}/invoke"| invoke
    strategy -->|"authorization query"| opa
    llm -->|"LLM API calls"| gemini
    specialist -->|"POST /answer"| ragapi
    ragapi --> db
    rm --> db
    keycloak -.->|"JWT validation"| identity
```

## Services

| Service | Port | Role |
|---------|------|------|
| PostgreSQL (pgvector) | 5433 | User data, sessions, audit logs, and vector storage for RAG |
| RAG API | 8003 | Semantic search over support tickets |
| Agent Service | 8001 | LLM-based routing and specialist agents |
| Request Manager | 8000 | AAA enforcement, A2A orchestration, chat API |
| OPA | 8181 | Policy engine for authorization (Rego policies) |
| Keycloak | 8090 | OIDC identity provider (user authentication) |
| Web UI (nginx) | 3000 | PatternFly chat interface |

> **Note:** Ports above are for `make setup` (uses `scripts/setup.sh`). The `docker-compose.yaml` uses different host port mappings: PostgreSQL on 5432, RAG API on 8080. Internal container ports remain the same.

## Request Flow

1. **User sends message** -- Web UI sends `POST /adk/chat` with user email and message text.
2. **Identity & credential capture** -- `IdentityMiddleware` extracts SPIFFE identity (from `X-SPIFFE-ID` header in mock mode). JWT is decoded and stored in `CredentialService` for downstream propagation. Request Manager resolves user from PostgreSQL, loads departments.
3. **A2A call: routing-agent** -- Request Manager invokes `POST /api/v1/agents/routing-agent/invoke` via A2A, passing `transfer_context` with `departments` and `conversation_history`. Outbound call includes `X-SPIFFE-ID` header (service identity) but no delegation headers (this is a service-to-service call).
4. **Routing decision** -- Routing-agent's LLM classifies intent. Returns `ROUTE:software-support` or a conversational response.
5. **OPA authorization + scope reduction** -- If routing to a specialist, Request Manager queries OPA with `Delegation(user_spiffe_id, agent_spiffe_id, user_departments)`. OPA computes `User Departments ∩ Agent Capabilities`. Blocked if intersection is empty. The **effective departments** (intersection result) replace the user's full departments in the downstream `transfer_context`.
6. **A2A call: specialist agent** -- Request Manager invokes the specialist via A2A with delegation headers (`X-Delegation-User`, `X-Delegation-Agent`), JWT, and the narrowed `effective_departments`. Agent-service verifies caller identity via SPIFFE and re-checks OPA authorization (defense-in-depth). Specialist queries RAG API, gets matching tickets, builds LLM prompt with RAG context, returns grounded response.
7. **Audit** -- `_complete_request_log()` updates `request_logs` with `agent_id`, `response_content`, `processing_time_ms`, `completed_at`.
8. **Response** -- Request Manager stores conversation turn in `request_sessions.conversation_context`, returns response to the UI.

## Key Design Decisions

- **Single-turn routing:** The routing-agent classifies intent in one LLM call (no multi-turn state machine). Returns `ROUTE:<agent>` or a conversational response.
- **Mandatory RAG:** Specialist agents always query the RAG API. If RAG is unavailable, the request fails (no silent degradation).
- **OPA + permission intersection:** Authorization uses `User Departments ∩ Agent Capabilities` evaluated by OPA. The LLM can't bypass the OPA hard gate.
- **Full audit:** Every A2A call records which agent handled the request, the response, and processing time.
- **A2A exclusively:** No message brokers. Agents communicate via synchronous HTTP calls.
- **Pluggable LLM:** Backend configured via `LLM_BACKEND` env var. Supports Gemini (default in setup), OpenAI, and Ollama.

## Conversation Context

Each chat session maintains conversation history in `request_sessions.conversation_context.messages`:

```json
{
  "messages": [
    {"role": "user", "content": "My app crashes with error 500"},
    {"role": "assistant", "content": "...", "agent": "software-support"},
    {"role": "user", "content": "It happens when I click submit"},
    {"role": "assistant", "content": "...", "agent": "software-support"}
  ]
}
```

- **Sent to routing-agent:** Last 20 messages (for intent classification with context)
- **Sent to specialist agents:** Last 10 messages (for follow-up handling)
- **Max stored:** 40 messages (oldest trimmed)

## Agent Configuration

Agents are defined in `agent-service/config/agents/*.yaml` and loaded by `ResponsesAgentManager` at startup.

| Field | Purpose |
|-------|---------|
| `name` | Agent registration key. Must match the name used in `/invoke` URL. |
| `llm_backend` | Which LLM provider to use (gemini, openai, ollama). |
| `llm_model` | Model name passed to the provider. |
| `system_message` | System prompt prepended to every LLM call. |
| `sampling_params.strategy.type` | Sampling strategy (e.g., `top_p`). |
| `sampling_params.strategy.temperature` | Temperature for LLM calls. |
| `sampling_params.strategy.top_p` | Top-p (nucleus) sampling parameter. |

Example (`software-support-agent.yaml`):

```yaml
name: "software-support"
llm_backend: "gemini"
llm_model: "gemini-2.5-flash"
system_message: |
  You are a software support specialist...
sampling_params:
  strategy:
    type: "top_p"
    temperature: 0.7
    top_p: 0.95
```

Available agents:

| File | Agent Name | Role |
|------|-----------|------|
| `routing-agent.yaml` | `routing-agent` | Classifies user intent and routes to the correct specialist |
| `software-support-agent.yaml` | `software-support` | Resolves software issues using RAG-backed knowledge base |
| `network-support-agent.yaml` | `network-support` | Resolves network issues using RAG-backed knowledge base |

## Project Structure

```mermaid
flowchart TD
    root["agentic-partners-integration"]

    root --> as["agent-service/\nAI agent processing service"]
    as --> as_cfg["config/agents/\nAgent YAML configs\n(routing, software, network)"]
    as --> as_src["src/agent_service/"]
    as_src --> as_main["main.py — FastAPI app, /invoke endpoint, routing + RAG logic"]
    as_src --> as_agents["agents.py — Agent manager, LLM integration, config loading"]
    as_src --> as_llm["llm/ — Pluggable LLM clients (Gemini, OpenAI, Ollama)"]
    as_src --> as_schemas["schemas.py — Request/response models for /invoke"]

    root --> rm["request-manager/\nAAA enforcement, A2A orchestration"]
    rm --> rm_src["src/request_manager/"]
    rm_src --> rm_main["main.py — FastAPI app, IdentityMiddleware, session cleanup"]
    rm_src --> rm_adk["adk_endpoints.py — /adk/chat, /adk/audit (chat + audit API)"]
    rm_src --> rm_cs["communication_strategy.py — A2A invocation, OPA hard gate, audit"]
    rm_src --> rm_ac["agent_client_enhanced.py — HTTP client for A2A calls"]
    rm_src --> rm_cred["credential_service.py — Request-scoped credential management"]

    root --> rag["rag-service/\nRAG API (pgvector + Gemini embeddings)"]
    rag --> rag_svc["rag_service.py — FastAPI service for /answer endpoint"]
    rag --> rag_ingest["ingest_knowledge.py — Data ingestion script"]

    root --> ui["pf-chat-ui/\nPatternFly chat web UI"]
    ui --> ui_static["static/"]
    ui_static --> ui_index["index.html — Landing page (redirects to login or chat)"]
    ui_static --> ui_login["login.html — Login page with JWT authentication"]
    ui_static --> ui_chat["chat.html — Chat interface with PF6 components"]
    ui_static --> ui_audit["audit.html — Request audit log viewer"]
    ui_static --> ui_events["audit-events.html — Audit trail viewer"]
    ui --> ui_nginx["nginx.conf — Reverse proxy to request-manager"]

    root --> shared["shared-models/\nShared library: DB models, migrations, identity, OPA client"]
    root --> kc["keycloak/\nKeycloak realm config (OIDC)"]

    root --> pol["policies/\nOPA Rego policies (authorization rules)"]
    pol --> pol_user["user_permissions.rego — User-to-department mappings"]
    pol --> pol_agent["agent_permissions.rego — Agent capability mappings"]
    pol --> pol_deleg["delegation.rego — Permission intersection logic"]
    pol --> pol_test["delegation_test.rego — Policy tests"]

    root --> data["data/ — Synthetic support tickets (JSON)"]
    root --> scripts["scripts/ — Setup, build, test, and user management scripts"]
    root --> helm["helm/ — Helm chart for Kubernetes/OpenShift deployment"]
    root --> makefile["Makefile — Build, test, lint, and deploy targets"]
    root --> compose["docker-compose.yaml — Full stack compose file"]

    style root fill:#f5f5f5,stroke:#424242,font-weight:bold
    style as fill:#e8f5e9,stroke:#2e7d32
    style rm fill:#e3f2fd,stroke:#1565c0
    style rag fill:#fff3e0,stroke:#e65100
    style ui fill:#fce4ec,stroke:#c62828
    style shared fill:#f3e5f5,stroke:#6a1b9a
    style kc fill:#e0f2f1,stroke:#00695c
    style pol fill:#fffde7,stroke:#f9a825
```

## Container Images

| Image | Containerfile | Base Image | Contents |
|-------|--------------|------------|----------|
| `partner-agent-service:latest` | `agent-service/Containerfile` | UBI9 Python 3.12 | Agent service + shared-models |
| `partner-request-manager:latest` | `request-manager/Containerfile` | UBI9 Python 3.12 | Request manager + shared-models |
| `partner-rag-api:latest` | `rag-service/Containerfile` | UBI9 Python 3.12 | RAG API (pgvector + Gemini embeddings) |
| `partner-pf-chat-ui:latest` | `pf-chat-ui/Containerfile` | UBI9 nginx 1.24 | Static PatternFly UI + nginx reverse proxy |

All custom images use Red Hat UBI9 base images. Agent service and request manager use a multi-stage build: `registry.access.redhat.com/ubi9/python-312` (builder) / `ubi9/python-312-minimal` (runtime). RAG API uses `ubi9/python-312`. Chat UI uses `ubi9/nginx-124`.

## Database

PostgreSQL 16 with pgvector extension. Schema managed by Alembic (current version: 009).

### Core Tables

| Table | Purpose |
|-------|---------|
| `users` | SPIFFE identity, roles, `departments` (OPA authorization) |
| `request_sessions` | Session state, `conversation_context` (JSON message history) |
| `request_logs` | Full audit: request content, response content, agent_id, processing time, timestamps |
| `audit_events` | SOC 2 audit trail: authentication, authorization, and data-access events (append-only) |
| `alembic_version` | Migration tracking |

### Additional Tables

| Table | Purpose |
|-------|---------|
| `user_integration_configs` | Per-user integration configuration (WEB type) |
| `user_integration_mappings` | Maps users to external integration identifiers |

Agent conversation state is managed in-memory per request (stateless A2A calls).
