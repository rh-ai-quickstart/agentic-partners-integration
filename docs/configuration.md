# Configuration Reference

## Environment Variables

### LLM

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_BACKEND` | `openai` | LLM provider: `gemini`, `openai`, or `ollama`. Setup scripts set `gemini`. |
| `GOOGLE_API_KEY` | -- | Required when using Gemini backend |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model name for Gemini |
| `OPENAI_API_KEY` | -- | Required when using OpenAI backend |
| `OPENAI_MODEL` | -- | Model name for OpenAI (e.g., `gpt-4`) |
| `OLLAMA_BASE_URL` | -- | Ollama server URL (e.g., `http://localhost:11434`) |
| `OLLAMA_MODEL` | -- | Model name for Ollama |

### Database

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | -- | PostgreSQL connection string (`postgresql+asyncpg://...`) |

### A2A Communication

| Variable | Default | Description |
|----------|---------|-------------|
| `AGENT_SERVICE_URL` | `http://agent-service:8080` | Agent service base URL |
| `RAG_API_ENDPOINT` | `http://rag-api:8080/answer` | RAG API answer endpoint URL |
| `AGENT_TIMEOUT` | `120` | Timeout in seconds for A2A calls |
| `STRUCTURED_CONTEXT_ENABLED` | `true` | Send structured `transfer_context` in A2A calls |

### Identity & Authorization

| Variable | Default | Description |
|----------|---------|-------------|
| `MOCK_SPIFFE` | `true` | Use mock SPIFFE mode (X-SPIFFE-ID header) instead of real mTLS |
| `SPIFFE_TRUST_DOMAIN` | `partner.example.com` | SPIFFE trust domain for identity URIs |
| `OPA_URL` | `http://localhost:8181` | OPA policy engine URL for authorization queries |
| `ENFORCE_AGENT_AUTH` | `true` | Require caller SPIFFE identity on agent-service /invoke endpoint. Set to `false` for testing without identity headers. |
| `KEYCLOAK_URL` | `http://keycloak:8080` | Keycloak server URL for OIDC authentication |
| `KEYCLOAK_REALM` | `partner-agent` | Keycloak realm name |
| `KEYCLOAK_CLIENT_ID` | `partner-agent-ui` | Keycloak OIDC client ID |

### RAG Service

| Variable | Default | Description |
|----------|---------|-------------|
| `EMBEDDING_MODEL` | `models/gemini-embedding-001` | Embedding model for vector generation |
| `LLM_MODEL` | -- | LLM model used by RAG service for answer generation |

### Operations

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level for services |
| `SESSION_CLEANUP_INTERVAL_HOURS` | `24` | How often to run session cleanup |
| `INACTIVE_SESSION_RETENTION_DAYS` | `30` | Days to retain inactive sessions before cleanup |

## LLM Backends

| Backend | Env Vars | Notes |
|---------|----------|-------|
| Gemini | `GOOGLE_API_KEY`, `GEMINI_MODEL` | Used by default in setup. Uses Google AI API. |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` | GPT-4, GPT-3.5, etc. |
| Ollama | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | Local LLMs. No API key needed. |
