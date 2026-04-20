# A2A -- Agent-to-Agent Communication

All inter-agent communication uses exclusively HTTP-based A2A (Agent-to-Agent) calls. There is no message broker, no event bus, no shared memory -- agents talk directly over HTTP.

## Communication Pattern

```mermaid
sequenceDiagram
    participant RM as Request Manager<br/>(orchestrator)
    participant OPA as OPA
    participant AS as Agent Service<br/>(agents)

    RM->>AS: POST /api/v1/agents/routing-agent/invoke<br/>Headers: X-SPIFFE-ID (service identity)<br/>{session_id, user_id, message,<br/>transfer_context: {departments, history}}
    Note right of AS: Verify caller SPIFFE identity<br/>routing-agent classifies<br/>intent via LLM
    AS-->>RM: {content, routing_decision: "software-support"}

    RM->>OPA: check_agent_authorization<br/>(user depts ∩ agent caps)
    OPA-->>RM: allow: true, effective: ["software"]

    RM->>AS: POST /api/v1/agents/software-support/invoke<br/>Headers: X-SPIFFE-ID, X-Delegation-User,<br/>X-Delegation-Agent, Authorization: Bearer JWT<br/>{session_id, user_id, message,<br/>transfer_context: {departments: ["software"],<br/>history, previous_agent: "routing-agent"}}
    Note right of AS: Verify SPIFFE identity<br/>Re-check OPA (defense-in-depth)<br/>specialist queries RAG,<br/>generates grounded response
    AS-->>RM: {content: "Based on similar cases..."}
```

## How It Works

1. **`DirectHTTPStrategy`** in `communication_strategy.py` handles all A2A communication.
2. **Agent registry discovery:** On first request, `_ensure_registry()` calls `GET /api/v1/agents/registry` on the agent-service. The registry returns each specialist agent's departments and description. **Remote agents** (those with an `endpoint` field in their YAML config) also include their invoke URL. Local agents have no `endpoint` — the request-manager uses its default `AGENT_SERVICE_URL` for them.
3. **`EnhancedAgentClient`** (`agent_client_enhanced.py`) sends `POST /api/v1/agents/{agent_name}/invoke` with SPIFFE identity, delegation headers, and JWT. For **local agents**, the POST goes to the agent-service. For **remote agents**, the POST goes directly to the remote host URL from the registry — bypassing the agent-service for the specialist call.
4. **`transfer_context`** carries the user's `departments` (narrowed to effective scope after OPA), `conversation_history`, and `previous_agent` across each A2A call, so the receiving agent has full context.
5. **Two-hop routing:** The request-manager first invokes the routing-agent (service-to-service, no delegation). If the response contains a `routing_decision`, the request-manager queries OPA for authorization, reduces scope to `effective_departments`, then makes a second A2A call to the specialist agent with delegation headers.
6. **Defense-in-depth OPA enforcement:** The request-manager queries OPA before each specialist invocation (primary gate). The agent-service also verifies caller SPIFFE identity and re-checks OPA when delegation headers are present (secondary gate). If the primary gate is bypassed, the secondary gate blocks the request.
7. **Credential propagation:** `CredentialService` stores the user's JWT in request-scoped context vars. `outbound_identity_headers()` builds SPIFFE and delegation headers. Both are attached to outbound A2A calls by `EnhancedAgentClient`.
8. **Audit at every hop:** After each A2A call completes, `_complete_request_log()` records the responding agent, full response, and processing time in `request_logs`.

## Local vs Remote Agent Routing

Both agent types implement the same `POST /api/v1/agents/{name}/invoke` contract with the same request/response schema. The request-manager is unaware of the deployment model — it simply sends HTTP to the URL it obtained from the registry.

```mermaid
flowchart LR
    RM["Request Manager"]

    subgraph local["Agent Service (local agents)"]
        sw["/api/v1/agents/software-support/invoke"]
        nw["/api/v1/agents/network-support/invoke"]
    end

    subgraph remote["Kubernetes Partner Agent (remote)"]
        k8s["/api/v1/agents/kubernetes-support/invoke"]
    end

    RM -->|"default AGENT_SERVICE_URL\n(no endpoint in registry)"| local
    RM -->|"explicit endpoint URL\n(from registry)"| remote

    style local fill:#e8f5e9,stroke:#2e7d32
    style remote fill:#e8eaf6,stroke:#283593
```

The registry response format:

```json
{
  "agents": {
    "software-support": {
      "departments": ["software"],
      "description": "Handles software issues..."
    },
    "kubernetes-support": {
      "departments": ["kubernetes"],
      "description": "Handles Kubernetes issues...",
      "endpoint": "http://partner-kubernetes-agent-full:8080/api/v1/agents/kubernetes-support/invoke"
    }
  }
}
```

Agents **without** an `endpoint` field are local — the request-manager uses its default URL. Agents **with** an `endpoint` are remote — the request-manager routes directly to that URL.

## A2A Endpoint Contract

```mermaid
flowchart LR
    subgraph req["POST /api/v1/agents/{agent_name}/invoke"]
        direction TB

        subgraph headers["Headers · required when ENFORCE_AGENT_AUTH=true"]
            h1["X-SPIFFE-ID: str\ncaller's SPIFFE identity\n(mock header or mTLS cert)"]
            h2["Authorization: Bearer JWT\nuser's Keycloak JWT\n(optional, token propagation)"]
            h3["X-Delegation-User: str\nuser SPIFFE ID\n(specialist calls only → triggers OPA re-check)"]
            h4["X-Delegation-Agent: str\ntarget agent SPIFFE ID\n(specialist calls only)"]
        end

        subgraph body["Request Body"]
            b1["session_id: str — conversation session"]
            b2["user_id: str — user email"]
            b3["message: str — user message text"]
            subgraph tc["transfer_context · optional"]
                t1["departments: str[]\neffective department tags\n(narrowed by OPA intersection)"]
                t2["conversation_history: msg[]\nprior messages"]
                t3["previous_agent: str\nwhich agent handled the last turn"]
            end
        end

        subgraph response["Response"]
            r1["content: str\nagent's text response"]
            r2["routing_decision: str\n(routing-agent only)\nwhich specialist to delegate to"]
            r3["agent_name: str\nwhich agent produced the response"]
        end
    end

    style headers fill:#fff3e0,stroke:#e65100
    style body fill:#e8f5e9,stroke:#2e7d32
    style tc fill:#f1f8e9,stroke:#558b2f
    style response fill:#e3f2fd,stroke:#1565c0
```

## Why A2A Instead of an Event Bus

- **Simplicity:** No broker infrastructure to deploy and manage.
- **Synchronous responses:** The user waits for a response -- direct HTTP keeps the architecture straightforward.
- **Observability:** Each A2A call is a simple HTTP request with full audit. No message delivery guarantees to debug.
- **Horizontal scaling:** Agents are stateless HTTP services. Scale by adding replicas behind a load balancer.
