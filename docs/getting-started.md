# Getting Started

## Prerequisites

- Docker
- Google API Key (for Gemini LLM and embeddings)

## Setup

```bash
git clone https://github.com/rh-ai-quickstart/agentic-partners-integration
cd agentic-partners-integration
make setup
```

On first run it prompts for your Google API key and saves it to `.env`. Then it builds all container images, starts infrastructure (PostgreSQL with pgvector, Keycloak, OPA), runs database migrations, starts application services, ingests the RAG knowledge base into pgvector, and launches the web UI. At the end it verifies all services are healthy and prints login credentials.

## Services

| Service | URL |
|---------|-----|
| Web UI | http://localhost:3000 |
| Request Manager API | http://localhost:8000 |
| Agent Service | http://localhost:8001 |
| RAG API | http://localhost:8003 |
| Keycloak (admin) | http://localhost:8090 |
| OPA | http://localhost:8181 |

## Test Users

| User | Password | Departments | Access |
|------|----------|-------------|--------|
| carlos@example.com | carlos123 | engineering, software, kubernetes | Software + Kubernetes support |
| luis@example.com | luis123 | engineering, network | Network support only |
| sharon@example.com | sharon123 | engineering, software, network, kubernetes, admin | All agents |
| josh@example.com | josh123 | _(none)_ | No agents (restricted) |

## Try It

1. Open http://localhost:3000
2. Click **Carlos** (or enter `carlos@example.com` / `carlos123`) and sign in
3. Type: "My app crashes with error 500" -- Routes to software-support agent (local) with RAG context
4. Type: "My pod is in CrashLoopBackOff" -- Routes to kubernetes-support agent (remote) with RAG context
5. Type: "VPN not connecting" -- Denied (Carlos lacks the `network` department)
6. Log out, sign in as `sharon@example.com` / `sharon123` -- All queries work (has all departments)

## Run Tests

```bash
make test   # E2E tests covering all four pillars
```

## Next Steps

- [Architecture Overview](architecture.md) -- system diagram, request flow, design decisions
- [Security (AAA)](aaa-security.md) -- authentication, authorization, audit trail
- [Development Guide](development.md) -- Makefile targets, building, testing
- [Configuration Reference](configuration.md) -- environment variables, LLM backends
