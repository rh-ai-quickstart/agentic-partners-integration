# Configuration Reference

## Environment Variables

### LLM

#### New Configuration (Recommended)

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_PROVIDER` | `gemini` | LLM provider: `gemini`, `openai`, `ollama`, or `anthropic` |
| `AI_API_KEY` | -- | Universal API key for any provider |
| `AI_MODEL` | `gemini-2.5-flash` | Universal model name |
| `AI_GEMINI_API_KEY` | -- | Gemini-specific key (optional, for multi-provider setups) |
| `AI_OPENAI_API_KEY` | -- | OpenAI-specific key (optional, for multi-provider setups) |
| `AI_ANTHROPIC_API_KEY` | -- | Anthropic-specific key (optional, for multi-provider setups) |

#### Legacy Configuration (Deprecated)

These variables are maintained for backward compatibility but will be removed in a future release. Deprecation warnings are logged when used.

| Variable | Replacement | Description |
|----------|-------------|-------------|
| `LLM_BACKEND` | `AI_PROVIDER` | LLM provider: `gemini`, `openai`, or `ollama` |
| `GOOGLE_API_KEY` | `AI_API_KEY` or `AI_GEMINI_API_KEY` | Required when using Gemini backend |
| `GEMINI_MODEL` | `AI_MODEL` | Model name for Gemini |
| `OPENAI_API_KEY` | `AI_API_KEY` or `AI_OPENAI_API_KEY` | Required when using OpenAI backend |
| `OPENAI_MODEL` | `AI_MODEL` | Model name for OpenAI |
| `OLLAMA_BASE_URL` | -- | Ollama server URL (still in use) |
| `OLLAMA_MODEL` | `AI_MODEL` | Model name for Ollama |

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

### Recommended Configuration

| Backend | Env Vars | Example |
|---------|----------|----------|
| Gemini | `AI_PROVIDER=gemini`, `AI_API_KEY`, `AI_MODEL` | `AI_PROVIDER=gemini AI_API_KEY=xyz AI_MODEL=gemini-2.5-flash` |
| OpenAI | `AI_PROVIDER=openai`, `AI_API_KEY`, `AI_MODEL` | `AI_PROVIDER=openai AI_API_KEY=sk-xyz AI_MODEL=gpt-4` |
| Ollama | `AI_PROVIDER=ollama`, `AI_MODEL`, `OLLAMA_BASE_URL` | `AI_PROVIDER=ollama AI_MODEL=llama3.1 OLLAMA_BASE_URL=http://localhost:11434` |

### Legacy Configuration (Deprecated)

| Backend | Env Vars | Notes |
|---------|----------|-------|
| Gemini | `GOOGLE_API_KEY`, `GEMINI_MODEL` | ⚠️ Deprecated. Use `AI_API_KEY` and `AI_MODEL` instead. |
| OpenAI | `OPENAI_API_KEY`, `OPENAI_MODEL` | ⚠️ Deprecated. Use `AI_API_KEY` and `AI_MODEL` instead. |
| Ollama | `OLLAMA_BASE_URL`, `OLLAMA_MODEL` | ⚠️ Partially deprecated. Use `AI_MODEL` instead of `OLLAMA_MODEL`. |

### Migration Guide

**From legacy to new configuration:**

```bash
# Old (deprecated)
export LLM_BACKEND=gemini
export GOOGLE_API_KEY=your-key
export GEMINI_MODEL=gemini-2.5-flash

# New (recommended)
export AI_PROVIDER=gemini
export AI_API_KEY=your-key
export AI_MODEL=gemini-2.5-flash
```

**Both configurations work during the transition period**, but deprecation warnings will be logged when legacy variables are used.
