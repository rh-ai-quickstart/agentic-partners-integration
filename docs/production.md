# Production Recommendations

The stack uses lightweight, easy-to-run components for local development. Below are the recommended production-grade alternatives for each service.

## Vector Database: pgvector

The system uses PostgreSQL with the pgvector extension for vector storage. This consolidates both application data and vector embeddings into a single database service.

| | Current Implementation | Production Enhancements |
|---|---|---|
| **Service** | pgvector (PostgreSQL 16) | Same -- pgvector in production PostgreSQL |
| **Benefits** | ACID transactions, RBAC, backups, replication -- all inherited from PostgreSQL. Unified data stack. | Maintain these benefits with production-grade PostgreSQL (see below) |
| **Index type** | No vector index (sequential scan) -- see dimension limitation below | HNSW or IVFFlat index if using ≤2000 dimensions; otherwise sequential scan or migrate to specialized vector DB |
| **Scale ceiling** | ~10,000 documents without index (acceptable for POC) | With index: ~10M vectors. Without index: migrate to specialized vector DB beyond 10,000 documents |
| **Monitoring** | Standard PostgreSQL metrics | Add pgvector-specific metrics: index build time, query latency percentiles |

### Embedding Dimension Limitation (Critical for Production)

**Current Constraint:** The system uses Google Gemini's `gemini-embedding-001` model which produces **3072-dimensional embeddings**. However, PostgreSQL pgvector has a hard limit:

- **HNSW index**: Maximum 2000 dimensions
- **IVFFlat index**: Maximum 2000 dimensions

**Impact on Current System:**
- **POC (current state)**: No vector index -- uses sequential scan through all 240 documents
- **Performance**: Acceptable for datasets under ~10,000 documents (searches complete in milliseconds)
- **Scalability**: Sequential scan becomes slow beyond 10,000+ documents

**Production Solutions:**

| Strategy | When to Use | Trade-offs | Implementation |
|----------|-------------|------------|----------------|
| **Option 1: Lower-Dimension Model** | Best for staying with pgvector + wanting index performance | Slightly lower embedding quality (often negligible) | Switch `EMBEDDING_MODEL=models/text-embedding-004` (768 dims), re-run migrations with `Vector(768)`, rebuild index, re-ingest data |
| **Option 2: Dimensionality Reduction** | Want to keep high-quality embeddings but need indexing | Adds PCA step; 95%+ accuracy retention typical | Apply PCA to reduce 3072→1536 dims post-embedding, update schema to `Vector(1536)`, enable HNSW index |
| **Option 3: Dedicated Vector DB** | Scaling beyond 100K documents or need <10ms p99 latency | Additional infrastructure; operational complexity | Migrate to **Weaviate** (no dimension limits, open source), **Pinecone** (fully managed), **Qdrant** (fast, production-ready), or **Milvus** (billion-scale) |
| **Option 4: Keep Sequential Scan** | POC/demo or datasets under 10K documents | No index performance benefits | No changes needed -- current setup is fine |

**Recommended Production Path:**

For most production deployments with the current dataset size (hundreds to low thousands of documents):

1. **Short term (< 10K documents)**: Keep current setup -- sequential scan is acceptable
2. **Medium term (10K-100K documents)**: Switch to a lower-dimension model (Option 1) and enable HNSW indexing
3. **Long term (> 100K documents)**: Migrate to a dedicated vector database (Option 3)

**Code Changes Required for Option 1 (Lower Dimensions):**

```python
# In rag-service/rag_service.py and ingest_knowledge.py
EMBEDDING_MODEL = "models/text-embedding-004"  # 768 dimensions instead of gemini-embedding-001
EMBEDDING_DIM = 768  # Update from 3072

# In shared-models/alembic/versions/004_add_knowledge_base_tables.py
Vector(768)  # Update from Vector(3072)

# Then re-create the table with HNSW index:
CREATE INDEX idx_knowledge_documents_embedding ON knowledge_documents
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
```

**Current Migration State:** The migration file (`004_add_knowledge_base_tables.py`) uses `Vector(3072)` and intentionally **skips index creation** due to the dimension limit. The comment in that file documents this decision.

## PostgreSQL: Harden for Production

The current setup uses default credentials, no TLS, and a single instance with no replication.

| | PoC (current) | Production |
|---|---|---|
| **Image** | `pgvector/pgvector:pg16` | CrunchyData PGO operator (Kubernetes) or managed PostgreSQL (RDS, Cloud SQL, Azure Database) |
| **Credentials** | Hardcoded `user`/`pass` | Rotated secrets via Vault, Kubernetes Secrets, or cloud IAM |
| **TLS** | Disabled | Required -- enable `sslmode=require` in connection strings |
| **High availability** | Single instance | Patroni-based HA (CrunchyData PGO) or Multi-AZ (cloud managed) |
| **Backups** | None | pgBackRest (CrunchyData) or automated cloud snapshots with point-in-time recovery |
| **Connection pooling** | Direct connections | PgBouncer sidecar or built-in cloud pooling |
| **Monitoring** | None | pg_stat_statements, Prometheus postgres_exporter |

## Keycloak: Production Mode

Keycloak runs in `start-dev` mode with an embedded H2 database that loses state on restart.

| | PoC (current) | Production |
|---|---|---|
| **Mode** | `start-dev` (H2 in-memory, no TLS, debug logging) | `start` (production mode) |
| **Database** | Embedded H2 (volatile) | External PostgreSQL (can share the existing cluster in a separate database) |
| **TLS** | Disabled | TLS termination at ingress or Keycloak's built-in TLS (`KC_HTTPS_*`) |
| **Clustering** | Single instance | 2+ replicas with Infinispan distributed cache |
| **Admin credentials** | `admin`/`admin123` | Strong password via secrets management; disable admin console in production |
| **Realm management** | `--import-realm` from JSON | Keycloak Admin API or Terraform keycloak provider for GitOps |

## OPA: Bundle Server + Decision Logging

OPA runs with locally mounted policy files and no audit trail.

| | PoC (current) | Production |
|---|---|---|
| **Policy delivery** | Volume-mounted `.rego` files | OPA bundle server (S3 bucket, HTTP server, or Styra DAS) for versioned policy distribution |
| **Decision logging** | None | Enable OPA decision logs to a central store (Elasticsearch, CloudWatch) for audit compliance |
| **Deployment** | Shared singleton container | Sidecar per service (eliminates network hop and single point of failure) |
| **Policy testing** | `delegation_test.rego` | CI pipeline with `opa test` and `conftest` for policy-as-code validation |
| **Management** | Manual | Styra DAS (commercial) for policy impact analysis, testing, and rollback |

## LLM Backend: Gateway + Failover

The system calls Google Gemini directly with no retry logic, rate limiting, or provider failover.

| | PoC (current) | Production |
|---|---|---|
| **Provider** | Google Gemini (gemini-2.5-flash) direct API | LiteLLM proxy or cloud-managed endpoint (Vertex AI, AWS Bedrock) |
| **Failover** | None -- Gemini outage = system down | LiteLLM provides automatic failover across providers (Gemini → OpenAI → Anthropic) |
| **Rate limiting** | None | LiteLLM or API gateway (Kong, Envoy) with per-user token budgets |
| **Cost tracking** | None | LiteLLM tracks cost per request; or cloud provider billing dashboards |
| **Data residency** | Data sent to Google AI API | Vertex AI (keeps data in GCP project), or self-hosted via vLLM with open-weight models (Llama, Mistral) |
| **Retry/circuit breaker** | None | Add tenacity retries with exponential backoff; circuit breaker for sustained failures |

> **Note:** The existing `LLM_BACKEND` env var already supports Gemini, OpenAI, and Ollama. A LiteLLM proxy unifies these behind a single OpenAI-compatible endpoint with automatic fallback.

## Web UI: TLS + Security Headers

The nginx container serves static files over plain HTTP with no security headers.

| | PoC (current) | Production |
|---|---|---|
| **TLS** | Plain HTTP | TLS termination at ingress controller (Kubernetes) or Caddy (automatic Let's Encrypt) |
| **Security headers** | None | `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, `Strict-Transport-Security` |
| **Compression** | Disabled | Enable gzip/brotli in nginx for static assets |
| **Caching** | No cache headers | `Cache-Control` with content hashing for CSS/JS |
| **Build pipeline** | Raw HTML + inline JS | Consider React + `@patternfly/react-core` for a production UI with proper bundling, minification, and CSP compliance |

## RAG Service: Caching + Reranking

The RAG service has no caching, no reranking, and uses a one-time ingestion script.

| | PoC (current) | Production |
|---|---|---|
| **Response caching** | None -- every query re-embeds and re-searches | Redis or in-memory LRU cache for repeated queries |
| **Reranking** | None -- raw cosine similarity | Cross-encoder reranker (Cohere Rerank, or a local `cross-encoder/ms-marco-MiniLM-L-6-v2`) to improve retrieval precision |
| **Ingestion** | One-time `ingest_knowledge.py` script | Incremental pipeline: watch for new documents, embed delta, update vectors |
| **Document management** | Static JSON files | Document versioning with metadata (source, timestamp, category) for traceability |
| **Evaluation** | None | Retrieval metrics (MRR, NDCG) and answer quality evaluation (RAGAS, or LLM-as-judge) |

## Summary

| Service | PoC | Production | Priority |
|---------|-----|------------|----------|
| **Vector Embeddings** | **3072-dim (no index, sequential scan)** | **Switch to ≤2000-dim model + index** OR **migrate to dedicated vector DB** | **High** (if scaling > 10K docs) |
| PostgreSQL | Single instance, no TLS | **CrunchyData PGO or managed DB** (TLS, HA, backups) | High |
| Keycloak | `start-dev`, H2, no TLS | **Production mode** (external PG, TLS, clustering) | High |
| OPA | Mounted files, no logging | **Bundle server + decision logs** | Medium |
| LLM (Gemini) | Direct API, no failover | **LiteLLM proxy** (failover, rate limiting, cost tracking) | Medium |
| Web UI (nginx) | Plain HTTP, no headers | **TLS + security headers** (Caddy or hardened nginx) | Medium |
| RAG Service | No cache, no reranking | **Add Redis cache + reranker** | Low |
