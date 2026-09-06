# Architecture Diagram

```mermaid
flowchart TB
    subgraph User["User Layer"]
        Browser["Web Browser<br/>(PatternFly UI)"]
    end

    subgraph Auth["Authentication & Authorization"]
        Keycloak["Keycloak<br/>OIDC Provider"]
        OPA["Open Policy Agent<br/>Policy Enforcement"]
    end

    subgraph Core["Core Services"]
        RequestMgr["Request Manager<br/>API Gateway"]
        Router["AI Routing Agent<br/>Intent Classification"]
    end

    subgraph Agents["Specialist Agents"]
        SoftwareAgent["Software Support<br/>Agent"]
        NetworkAgent["Network Support<br/>Agent"]
        K8sAgent["Kubernetes Support<br/>Agent"]
    end

    subgraph Knowledge["Knowledge Layer"]
        RAG["RAG API<br/>Vector Search"]
        PGVector["PostgreSQL<br/>pgvector"]
    end

    subgraph LLM["LLM Backend"]
        Ollama["Ollama<br/>(Llama 3.2)"]
        External["External APIs<br/>(OpenAI/Gemini)"]
    end

    subgraph Security["Security Services"]
        SPIRE["SPIRE Server<br/>Workload Identity"]
        Audit["Audit Logger<br/>Request Tracking"]
    end

    Browser --> |"HTTPS + JWT"| RequestMgr
    RequestMgr --> |"Authenticate"| Keycloak
    RequestMgr --> |"Check Policy"| OPA
    RequestMgr --> |"Route Request"| Router
    Router --> |"A2A HTTP"| SoftwareAgent
    Router --> |"A2A HTTP"| NetworkAgent
    Router --> |"A2A HTTP"| K8sAgent
    
    SoftwareAgent --> |"Query Knowledge"| RAG
    NetworkAgent --> |"Query Knowledge"| RAG
    K8sAgent --> |"Query Knowledge"| RAG
    
    RAG --> |"Vector Search"| PGVector
    
    Router --> |"Generate Response"| Ollama
    SoftwareAgent --> |"Generate Response"| Ollama
    NetworkAgent --> |"Generate Response"| Ollama
    K8sAgent --> |"Generate Response"| Ollama
    
    Router -.-> |"Alternative"| External
    SoftwareAgent -.-> |"Alternative"| External
    
    RequestMgr --> |"Identity"| SPIRE
    SoftwareAgent --> |"Identity"| SPIRE
    NetworkAgent --> |"Identity"| SPIRE
    K8sAgent --> |"Identity"| SPIRE
    
    RequestMgr --> |"Log Events"| Audit
    Router --> |"Log Events"| Audit
    SoftwareAgent --> |"Log Events"| Audit

    style Browser fill:#e3f2fd,stroke:#1565c0,stroke-width:2px
    style Keycloak fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style OPA fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style RequestMgr fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style Router fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style SoftwareAgent fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style NetworkAgent fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style K8sAgent fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    style RAG fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px
    style PGVector fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px
    style Ollama fill:#fff9c4,stroke:#f57f17,stroke-width:2px
    style SPIRE fill:#fce4ec,stroke:#c62828,stroke-width:2px
```

## Component Description

### User Layer
- **Web Browser**: PatternFly-based React UI for user interaction

### Authentication & Authorization
- **Keycloak**: OpenID Connect provider for user authentication
- **OPA**: Policy-as-code engine enforcing department access rules

### Core Services
- **Request Manager**: API gateway handling all incoming requests
- **Router**: AI agent that classifies user intent and routes to specialists

### Specialist Agents
- **Software Support**: Handles application errors, crashes, and software issues
- **Network Support**: Handles VPN, connectivity, and network problems
- **Kubernetes Support**: Handles pod, deployment, and cluster issues

### Knowledge Layer
- **RAG API**: Vector search over historical support tickets
- **PostgreSQL + pgvector**: Stores embeddings and ticket data

### LLM Backend
- **Ollama (Default)**: Local open-weight model (Llama 3.2)
- **External APIs**: Optional OpenAI, Gemini, or Anthropic

### Security Services
- **SPIRE**: SPIFFE workload identity for service-to-service auth
- **Audit Logger**: Tracks all requests for compliance and debugging
