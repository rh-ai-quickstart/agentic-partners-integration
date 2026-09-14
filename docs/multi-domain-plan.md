# Multi-Domain Agentic Platform — Implementation Plan

> Red Hat as a composable sub-orchestrator within the Ericsson Phase 2 Multivendor Agentic AI framework.
> This document covers the 7-phase plan to extend the current single-domain (Support Resolution)
> implementation to a full 4-domain MAO architecture matching the slide architecture.

## Context

The current platform implements **one domain**: Support Resolution
(`routing-agent → kubernetes-support / network-support / software-support`).

The target architecture (from the MAO slide) is:

```
User Interface (Hybrid Console, Customer Portal, Docs, Account)
         │
    REST / SSE / HTTL
         │
    MAO API  ←  request-manager (already built)
         │
   ┌─────┴──────────────────────────────────┐
   │             │             │            │
Subscription  Docs         Support      Infrastructure
  Mgmt       Assistant    Resolution    Management
   │             │        (DONE ✓)          │
Entitlements  Search      kubernetes     Cluster
Renewal       Explainer   network        RHEL
Usage         Examples    software       Insights
```

The codebase is **more ready than expected** — the routing loop, A2A registry, and
agent mounts are already generic. Most work is YAML configs, OPA policy extension,
and the cross-domain token exchange layer.

---

## Summary

| | |
|---|---|
| **Total phases** | 7 |
| **Estimated duration** | 35–48 days |
| **New specialist agents** | 12 (3 per new domain) |
| **New orchestrators** | 4 (one per domain) |
| **New Rego files** | 1 (`cross_domain_policy.rego`) |
| **New DB migrations** | 2 (011, 012) |
| **New Python modules** | 3 (`domain_config.py`, `cross_domain_token_exchange.py`, `workflow_token_service.py`) |

---

## Phase 1 — Agent YAML Schema Extension and AgentManager Refactor

**Complexity:** Low · **Estimate:** 2–3 days · **Breaks nothing — purely additive**

### Goal

Introduce `role` and `domain` fields into the YAML schema, make `AgentManager`
role-aware, fix the per-request reinstantiation anti-pattern, and update the agent
registry endpoint to expose domain grouping.

### Deliverables

- `role` field (`router | orchestrator | specialist`) recognized in all YAML configs
- `domain` field recognized and indexed by `AgentManager`
- `AgentManager.get_specialists_by_domain(domain)` method
- `AgentManager.get_orchestrator_agents()` method
- `AgentManager.get_orchestrator_descriptions()` and `get_orchestrator_dept_map()` methods
- `AgentManager` becomes a module-level singleton (initialized at app startup, not per-request)
- `/api/v1/agents/registry` returns domain grouping and role field
- Existing 3 support specialists and routing-agent YAML files updated with `role:` + `domain:`

### Modified Files

| File | Change |
|---|---|
| `agent-service/config/agents/routing-agent.yaml` | Add `role: router` |
| `agent-service/config/agents/kubernetes-support-agent.yaml` | Add `role: specialist`, `domain: support-resolution` |
| `agent-service/config/agents/network-support-agent.yaml` | Add `role: specialist`, `domain: support-resolution` |
| `agent-service/config/agents/software-support-agent.yaml` | Add `role: specialist`, `domain: support-resolution` |
| `agent-service/src/agent_service/agents.py` | Role-aware query methods, module-level singleton |
| `agent-service/src/agent_service/main.py` | Replace hardcoded `agent_name == "routing-agent"` check with role-aware dispatch |

### Key Decisions

> **DECISION NEEDED:** Single agent-service process (all 4 domains) vs one container per domain.
> Separate processes give independent scaling and fault isolation but require 4× more containers.
> **Recommendation:** single process for Phases 1–6, split to multi-process in Phase 7.

> **DECISION NEEDED:** The `AgentManager` singleton is safe because YAML configs are read-only
> after startup. If hot-reload of configs is ever needed, a background refresh task must be
> designed separately.

> `routing-agent.yaml` gets `role: router` (distinct from `orchestrator`) to preserve its
> existing cross-domain dispatch behavior. Domain orchestrators get `role: orchestrator` and a
> `domain` field scoping them to their own specialists.

### Dependencies

None — this phase is the foundation for all others.

---

## Phase 2 — Domain Orchestrator Dispatch and MAO API Topology Change

**Complexity:** Medium · **Estimate:** 4–5 days

### Goal

Extend the `invoke_agent` endpoint to handle agents with `role: orchestrator` the same
way `routing-agent` is handled today — executing an LLM-based `ROUTE:` delegation loop
but scoped to that domain's specialists only. Update the routing-agent system message to
enumerate 4 domain orchestrators as routing targets. Fix the session-pinning cross-domain
boundary bug. Raise `max_routing_hops` from 5 to 8.

### Deliverables

- `invoke_agent` endpoint dispatches based on `role` from agent config (not hardcoded name string)
- Domain orchestrators execute `ROUTE:` loop restricted to their own domain's specialists
- routing-agent system message enumerates 4 domains with descriptions so LLM emits correct routing values
- Session pinning (`session.current_agent_id`) reset when user message crosses domain boundary
- `max_routing_hops` raised from 5 → 8 in `communication_strategy.py`
- 4 orchestrator YAML stubs deployed and mounted in A2A discovery endpoint
- `/.well-known/agent-card.json` returns hierarchical `domain → orchestrator → specialists` grouping

### New Files

```
agent-service/config/agents/support-resolution-orchestrator.yaml
agent-service/config/agents/subscription-mgmt-orchestrator.yaml
agent-service/config/agents/docs-assistant-orchestrator.yaml
agent-service/config/agents/infra-mgmt-orchestrator.yaml
```

### Modified Files

| File | Change |
|---|---|
| `agent-service/src/agent_service/main.py` | Role-aware invoke branch for orchestrators |
| `agent-service/config/agents/routing-agent.yaml` | System message enumerates 4 domains |
| `request-manager/src/request_manager/communication_strategy.py` | Hop limit 5→8, session-pinning fix |
| `agent-service/src/agent_service/agents.py` | Domain-scoped specialist lookup for orchestrators |

### Key Decisions

> **DECISION NEEDED:** Session reset strategy when user crosses domain boundary.
> Option A: `domain_of_agent` lookup table detects mismatch.
> Option B: Clear `session.current_agent_id` when session was last active > N minutes ago.
> **Recommendation:** Option B — simpler, avoids cross-domain state management in Phase 2.

> **DECISION NEEDED:** Domain orchestrators' routing system messages must enumerate their
> specialists. The dynamic builder in `main.py` already does this from YAML — same builder
> can be reused filtering by `domain` field. Confirm before implementing.

> `support-resolution-orchestrator.yaml` must set `domain: support-resolution` and
> `departments: [kubernetes, network, software]` so OPA capability union works correctly.

### Dependencies

- Phase 1 complete and deployed

---

## Phase 3 — OPA Cross-Domain Authorization Policy

**Complexity:** High · **Estimate:** 5–6 days

### Goal

Add `orchestrator` and `cross_domain_orchestrator` identity types to the OPA policy
stack. Add Rules 7–9 for orchestrator→specialist and orchestrator→cross-domain-orchestrator
call patterns. Create `cross_domain_policy.rego`. Extend SPIFFE validation for federated
trust domains. Extend `agent_permissions.rego` with all 4 domain entries.

### Deliverables

- `parse_spiffe_type` in `delegation.rego` recognizes `domain_orchestrator` (path contains
  `/orchestrator/` in local trust domain) and `cross_domain_orchestrator` (external trust domain)
- **Rule 7:** `domain_orchestrator` + delegation calling a specialist → allow when orchestrator
  domain matches specialist capability domain and `user_depts ∩ specialist_caps ≠ ∅`
- **Rule 8:** orchestrator calling `cross_domain_orchestrator` → allow when caller is in
  `trusted_domain_peers[target]` and delegation chain is intact
- **Rule 9:** `cross_domain_orchestrator` without delegation → always denied
- `cross_domain_policy.rego` (new) with `trusted_domain_peers` nested map, `orchestrator_scope`
  map, and `allow_cross_domain_call` rule
- `spiffe_validation.rego` extended with `valid_trust_domains` set and `is_orchestrator_spiffe_id` helper
- `delegation_chain_validation.rego` extended with cross-domain hop validator
- `agent_permissions.rego` extended with entries for all 4 orchestrators using
  SPIFFE pattern `spiffe://partner.example.com/orchestrator/{domain-name}`
- `_valid_departments` extended to include: `subscription, documentation, cluster, rhel, insights`
- `agent_capabilities` map extended with 4 orchestrator entries

### New Files

```
policies/cross_domain_policy.rego
```

### Modified Files

| File | Change |
|---|---|
| `policies/delegation.rego` | Rules 7–9 for orchestrator and cross-domain identity types |
| `policies/agent_permissions.rego` | 4 orchestrator entries + new department names |
| `policies/spiffe_validation.rego` | Federated trust domain support |
| `policies/delegation_chain_validation.rego` | Cross-domain hop validator |
| `policies/token_exchange.rego` | Cross-domain token exchange authorization |

### Key Decisions

> **DECISION NEEDED:** SPIFFE ID path convention for orchestrators.
> Option A: `spiffe://partner.example.com/orchestrator/support-resolution` (path-based)
> Option B: `spiffe://partner.example.com/domain/support-resolution/orchestrator` (hierarchical)
> **Pick one and apply consistently** across `seed-services.sh`, YAML configs, and OPA before
> writing any Rego. The `is_orchestrator_spiffe_id` helper must match what the containers actually get.

> **DECISION NEEDED:** The delegation input schema has no `caller_orchestrator_spiffe_id` field.
> Cross-domain Rule 8 needs to know the source trust domain.
> Option A: add nullable `caller_orchestrator_spiffe_id` to OPA input in `opa_client.py` (breaking schema change).
> Option B: parse trust domain from `caller_spiffe_id` in Rego (fragile if path structure changes).
> **Recommendation:** Option A with a nullable field.

> `trusted_domain_peers` in `cross_domain_policy.rego` is **static Rego data** in Phase 3.
> In production it is replaced by `domain_registry` DB table in Phase 6. Hardcode 4 domains
> as mutually trusted peers and document the tech debt explicitly.

### Dependencies

- Phase 2 complete (orchestrator YAML files define the SPIFFE IDs)

---

## Phase 4 — Cross-Domain Token Exchange and Multi-Hop Act Chain Fix

**Complexity:** High · **Estimate:** 6–8 days

### Goal

Fix the DCR/`existing_act_claim` mutual exclusion bug. Add `DomainConfig` dataclass.
Add `CrossDomainTokenExchangeClient`. Wire SPIRE JWT-SVIDs as cross-domain bearer
credentials. Add `requested_audience` parameter to `exchange_for_agent()`.

### Critical Bug Fixed in This Phase

> **`token_exchange.py` lines 286–301:** when DCR `actor_token` is obtained,
> `existing_act_claim` is **silently discarded**. Every multi-hop delegation chain with
> DCR enabled drops the act chain after the first hop. **This is broken in production today.**
>
> Fix: forward `existing_act_claim` as a nested `act` within the outgoing payload alongside
> the DCR `actor_token`. They are complementary, not mutually exclusive — the DCR actor token
> proves service identity; the nested act chain proves the delegation lineage.

### Deliverables

- Bug fix: `token_exchange.py` — `existing_act_claim` preserved and nested correctly when
  DCR `actor_token` is present
- `DomainConfig` dataclass in `shared-models/src/shared_models/domain_config.py`
  — fields: `keycloak_url, realm, client_id, client_secret, spiffe_trust_domain, dcr_client`
  — loaded from env vars: `DOMAIN_{UPPER_NAME}_{FIELD}` (e.g. `DOMAIN_SUBSCRIPTION_KEYCLOAK_URL`)
- `CrossDomainTokenExchangeClient` in `request-manager/src/request_manager/cross_domain_token_exchange.py`
  — `exchange_cross_domain(subject_token, target_agent, existing_act_claim)`:
    1. Fetch SPIRE JWT-SVID with `audience=target_domain.keycloak_url`
    2. Present JWT-SVID to target realm token endpoint as `actor_token`
    3. Return target-realm-issued token with preserved act chain
- `exchange_for_agent()` updated with optional `requested_audience: str` parameter (RFC 8693 §2.1)
- `DCRClient._dcr_clients` dict re-keyed from `spiffe_id` → `(spiffe_id, realm_url)` tuple
  to allow same SPIFFE ID to hold registrations in multiple Keycloak realms

### New Files

```
shared-models/src/shared_models/domain_config.py
request-manager/src/request_manager/cross_domain_token_exchange.py
```

### Modified Files

| File | Change |
|---|---|
| `request-manager/src/request_manager/token_exchange.py` | Bug fix + `requested_audience` param |
| `shared-models/src/shared_models/dcr_client.py` | Re-key `_dcr_clients` by `(spiffe_id, realm_url)` |
| `shared-models/src/shared_models/spire_client.py` | Wire `fetch_jwt_svid(audience)` into cross-domain flow |
| `request-manager/src/request_manager/communication_strategy.py` | Use `CrossDomainTokenExchangeClient` |

### Key Decisions

> **DECISION NEEDED:** Single Keycloak realm (all 4 domains, audience-scoped) vs multi-realm
> (each domain has its own Keycloak instance requiring identity brokering).
> **Recommendation:** single realm for Phase 4, multi-realm deferred to production hardening.
> With single realm, `CrossDomainTokenExchangeClient` reduces to a within-realm audience-scoped
> exchange — far simpler to implement and operate.

> SPIRE JWT-SVID audience must match a value that the target realm's OIDC provider accepts.
> `fetch_jwt_svid(audience)` must pass `target_domain.keycloak_url` as the audience string
> unless a separate SPIRE-level audience is configured.

### Dependencies

- Phase 2 complete (domain orchestrators exist to make cross-domain calls)
- Phase 3 complete (OPA will authorize cross-domain hops)

---

## Phase 5 — New Domain Implementations: Subscription, Docs, Infrastructure

**Complexity:** Medium · **Estimate:** 5–7 days

### Goal

Create the 12 new specialist YAML files (3 per new domain), finalize the 4 orchestrator
YAMLs with complete system messages and A2A skill definitions, update routing-agent system
message with all 4 domain descriptions, update OPA capabilities map, add `rag_enabled`
field to YAML schema.

### New Files (12 specialist YAMLs)

```
# Subscription Management
agent-service/config/agents/entitlements-agent.yaml   (domain: subscription-management, departments: [subscription])
agent-service/config/agents/renewal-agent.yaml
agent-service/config/agents/usage-agent.yaml

# Docs Assistant
agent-service/config/agents/search-agent.yaml         (domain: docs-assistant, departments: [documentation])
agent-service/config/agents/explainer-agent.yaml
agent-service/config/agents/examples-agent.yaml

# Infrastructure Management
agent-service/config/agents/cluster-mgmt-agent.yaml  (domain: infra-management, departments: [infrastructure])
agent-service/config/agents/rhel-agent.yaml
agent-service/config/agents/insights-agent.yaml
```

### Modified Files

| File | Change |
|---|---|
| `agent-service/config/agents/routing-agent.yaml` | System message enumerates all 4 domains |
| `agent-service/config/agents/*-orchestrator.yaml` | Full system messages + A2A skills |
| `agent-service/config/agents/kubernetes-support-agent.yaml` | Add `role: specialist`, `domain: support-resolution` |
| `agent-service/config/agents/network-support-agent.yaml` | Add `role: specialist`, `domain: support-resolution` |
| `agent-service/config/agents/software-support-agent.yaml` | Add `role: specialist`, `domain: support-resolution` |
| `policies/agent_permissions.rego` | 12 new specialists + 4 orchestrators + new department names |
| `scripts/seed/seed-keycloak.sh` | New Keycloak roles for: `subscription, documentation, cluster, rhel, insights` |

### Key Decisions

> **DECISION NEEDED:** RAG knowledge base partitioning strategy.
> Option A: one collection per domain (4 collections, `RAG_COLLECTION` env var per container)
> Option B: single collection with `domain` metadata filter (passed as RAG query parameter)
> **Recommendation:** Option B for PoC — add `collection_filter` field to specialist YAML.

> **DECISION NEEDED:** `docs-assistant` specialists likely need a different RAG retrieval
> strategy — semantic search over documentation corpora, not ticket knowledge bases.
> Confirm whether the existing RAG API supports multiple corpora or if a separate RAG
> endpoint is needed for documentation.

> `examples-agent` and `explainer-agent` may not need RAG. Add `rag_enabled: false`
> to YAML schema — `main.py` skips RAG calls for agents with this flag. Orchestrators
> and `role: router` agents also get `rag_enabled: false` implicitly.

### Dependencies

- Phase 2 complete (domain orchestrator dispatch must work before specialists are useful)
- Phase 3 deployed (OPA must authorize new department names)

---

## Phase 6 — Async Cross-Domain Workflows and Database Foundation

**Complexity:** High · **Estimate:** 7–10 days

### Goal

Add DB migrations 011 and 012 for `domain_registry`, `cross_domain_workflow_jobs`, and
`cross_domain_audit_events` tables. Implement workflow token issuance and validation.
Add async job polling endpoint. This phase implements **Token Expiry Strategy 2**
(workflow token) for long-running cross-domain tasks.

### New Migrations

**`011_add_cross_domain_tables.py`** (down_revision=010):

```python
# domain_registry table
domain_id          UUID PK
domain_name        VARCHAR UNIQUE
display_name       VARCHAR
orchestrator_url   VARCHAR
spiffe_trust_domain VARCHAR
health_endpoint    VARCHAR
capabilities       JSON
is_active          BOOLEAN
last_health_check_at TIMESTAMP
last_health_status VARCHAR
registered_at      TIMESTAMP
updated_at         TIMESTAMP

# audit_events table additions (nullable, lock-free ALTER)
source_domain      VARCHAR(255)  -- nullable
correlation_id     VARCHAR(36)   -- nullable, indexed
```

**`012_add_workflow_tables.py`** (down_revision=011):

```python
# cross_domain_workflow_jobs table
job_id             UUID PK
workflow_token_hash VARCHAR(64)  -- SHA-256 hex, never raw token
origin_domain      VARCHAR
target_domain      VARCHAR FK domain_registry.domain_name
origin_session_id  UUID nullable FK request_sessions.session_id
status             WorkflowJobStatus ENUM (PENDING, RUNNING, COMPLETED, FAILED, TIMED_OUT)
workflow_type      VARCHAR
request_payload    JSON
response_payload   JSON
callback_url       VARCHAR nullable
correlation_id     VARCHAR(36) indexed
actor_spiffe_id    VARCHAR
started_at         TIMESTAMP
completed_at       TIMESTAMP nullable
expires_at         TIMESTAMP
error_code         VARCHAR nullable
error_message      TEXT nullable
created_at         TIMESTAMP
updated_at         TIMESTAMP

# cross_domain_audit_events table
full delegation_chain JSON per event
workflow_token_hash   VARCHAR(64)
```

### New Files

```
shared-models/alembic/versions/011_add_cross_domain_tables.py
shared-models/alembic/versions/012_add_workflow_tables.py
shared-models/src/shared_models/workflow_token_service.py
shared-models/src/shared_models/domain_config.py
scripts/seed/seed-domain-registry.py
```

`WorkflowTokenService`:
- `issue(job_id)` → opaque 32-byte base64url token (SHA-256 hash stored in DB, never raw)
- `validate(token)` → `job_id` or `None`
- `revoke(token)`

### New Request-Manager Endpoints

```
POST /api/v1/workflows
  Body: {target_domain, message, session_id, user}
  Returns: {job_id, workflow_token}

GET /api/v1/workflows/{job_id}/status
  Authorization: Bearer <workflow_token>
  Returns: {status, response_payload?, estimated_completion?}
```

### Modified Files

| File | Change |
|---|---|
| `shared-models/src/shared_models/models.py` | `WorkflowJobStatus` enum, new ORM classes |
| `request-manager/src/request_manager/communication_strategy.py` | DB-driven domain registry lookup |
| `request-manager/src/request_manager/main.py` | New workflow endpoints |
| `scripts/seed/seed-services.sh` | `seed-domain-registry.py` called in pipeline |

### Key Decisions

> **DECISION NEEDED:** Workflow token rotation policy.
> Option A: single-use (rotate on first successful validation, issue new token in status response)
> Option B: time-limited (enforced by `expires_at`, no rotation)
> **Recommendation:** Option B for Phase 6 simplicity, revisit for production.

> **DECISION NEEDED:** The chat UI currently expects a synchronous response. Async workflow
> dispatch requires the UI to poll or use SSE/WebSocket. Confirm before implementing dispatch.

> Lazy expiry (check `expires_at` at query time) is sufficient for Phase 6 — a background
> cleanup task can be added in Phase 8.

### Dependencies

- Phase 4 complete (`CrossDomainTokenExchangeClient` must exist before workflow dispatch)
- Phase 5 complete (domain names must be stable before seeding `domain_registry`)

---

## Phase 7 — Seed Scripts, SPIRE, and Container Infrastructure for All 4 Domains

**Complexity:** Medium · **Estimate:** 4–5 days

### Goal

Refactor `seed-services.sh` to use a `DOMAINS` array driving a loop. Assign unique
host ports, Docker labels, and SPIFFE IDs per domain. Update SPIRE entry-count guard
from 2 to 10. Wire `seed-domain-registry.py` into `setup.sh`.

### Container Layout

```
Domain              agent-service port   request-manager port  SPIFFE prefix
─────────────────── ──────────────────── ────────────────────  ─────────────────────────────────────
subscription-mgmt   8001                 8010                  spiffe://…/orchestrator/subscription-mgmt
docs-assistant      8002                 8011                  spiffe://…/orchestrator/docs-assistant
support-resolution  8003                 8012                  spiffe://…/orchestrator/support-resolution
infra-mgmt          8004                 8013                  spiffe://…/orchestrator/infra-mgmt
```

### seed-services.sh Refactor

```bash
DOMAINS=(subscription-mgmt docs-assistant support-resolution infra-mgmt)
AGENT_PORTS=(8001 8002 8003 8004)
RM_PORTS=(8010 8011 8012 8013)

for i in "${!DOMAINS[@]}"; do
    DOMAIN="${DOMAINS[$i]}"
    # unique Docker label, SPIFFE ID, and port per iteration
    docker run -d \
        --label "com.docker.compose.service=agent-service-${DOMAIN}" \
        -e "DOMAIN_FILTER=${DOMAIN}" \
        -p "${AGENT_PORTS[$i]}:8080" \
        partner-agent-service:latest
done
```

### Deliverables

- `DOMAINS` array loop in `seed-services.sh`
- Docker labels updated per domain (avoids SPIRE selector collision)
- Hierarchical SPIFFE IDs: `spiffe://partner.example.com/orchestrator/{domain}`,
  `spiffe://partner.example.com/specialist/{agent-name}`
- `setup.sh` SPIRE entry-count guard updated: `-ge 2` → `-ge 10`
- `seed-keycloak.sh` DCR IAT count raised: `100` → `500`
- `DOMAIN_FILTER` env var passed to each agent-service container (loads only that domain's YAMLs)
- `seed-domain-registry.py` called from `setup.sh` after migrations, before services
- OPA `opa put` commands in `setup.sh` step [6/8] to push new Rego files
- Port allocation table documented in `seed-services.sh` header comments

### Modified Files

| File | Change |
|---|---|
| `scripts/seed/seed-services.sh` | `DOMAINS` loop, unique labels + ports + SPIFFE IDs |
| `scripts/seed/seed-keycloak.sh` | IAT count 100 → 500 |
| `scripts/setup.sh` | SPIRE guard 2 → 10, call `seed-domain-registry.py` |
| `scripts/seed/seed-containers.sh` | No change needed |

### Key Decisions

> **DECISION NEEDED:** Single DCR IAT shared by all domain containers (shared registration
> budget) vs one IAT per domain (domain-level client isolation).
> **Recommendation:** shared IAT raised to 500 for PoC. Flag as tech debt for production.

> **DECISION NEEDED:** The Web UI has no domain selector. Two options:
> Option A: all domains route through a single top-level request-manager (single entry point, simpler UI)
> Option B: UI has a domain dropdown selecting which domain request-manager to call (matches slide)
> **Recommendation:** Option A for PoC — one routing-agent at the top level handles domain dispatch.

> SPIRE entry creation order matters — register orchestrator SPIFFE IDs before specialists.
> The `register_workload` loop must process orchestrator entries first.

### Dependencies

- Phase 5 complete (all YAML files must exist for containers to start correctly)
- Phase 6 complete (`domain_registry` table must exist for `seed-domain-registry.py`)
- Phase 3 complete (OPA policies must be deployed before any cross-domain call is made)

---

## Critical Risks

| Severity | Description | Fixed In |
|---|---|---|
| **CRITICAL** | `token_exchange.py` lines 286–301: DCR `actor_token` + `existing_act_claim` are mutually exclusive — delegation chain silently dropped after first hop. **Broken in production today.** | Phase 4 |
| **HIGH** | Session pinning (`session.current_agent_id`) routes cross-domain messages to the wrong specialist — silent mis-routing | Phase 2 |
| **HIGH** | OPA has no `orchestrator` identity type — cross-domain hops either over-permissive (Rule 1) or denied (Rule 6) until Phase 3 | Phase 3 |
| **HIGH** | Single `AGENT_SERVICE_URL` env var — stale endpoint data persists 5 min after deployment change; remote orchestrators rely on YAML `endpoint` field being correct | Phase 6 |
| **MEDIUM** | Docker label SPIRE selector collision — all domain containers get the same SVID if labels are not updated in Phase 7 | Phase 7 |
| **MEDIUM** | `scripts/sync_agent_capabilities.py` must exist and run in CI — if it does not, OPA denies all new agents (falls through to Rule 6) | Before Phase 5 |
| **MEDIUM** | Port conflict silent failure — `docker run` returns exit 0 even when the host port is taken; container exits immediately | Phase 7 |
| **LOW** | `max_routing_hops=8` has no loop detection — circular delegation exhausts counter with a generic error | Phase 2 |
| **LOW** | Workflow token column `format_version` not included — future token format changes require data migration | Phase 6 |
| **LOW** | Lazy expiry on `cross_domain_workflow_jobs` accumulates expired rows indefinitely | Phase 8 |

---

## Architecture Decisions

1. **Single Keycloak realm** for all 4 domains — cross-domain scoping via `audience` field
   in token exchange. Multi-realm deferred to production hardening.

2. **One agent-service container per domain** (multi-process) — different Docker label +
   SPIFFE ID per domain. Prevents a single agent outage from taking down all domains.
   Shares the same container image; differentiated by `DOMAIN_FILTER` env var.

3. **Hierarchical SPIFFE IDs:**
   ```
   spiffe://partner.example.com/router/routing-agent
   spiffe://partner.example.com/orchestrator/{domain-name}
   spiffe://partner.example.com/specialist/{agent-name}
   ```
   This gives OPA `parse_spiffe_type` a stable path segment to match without requiring
   a static allowlist of service names.

4. **`role` field in YAML replaces hardcoded name check** (`agent_name == "routing-agent"`).
   `role: router` preserves backward compatibility — existing behavior unchanged.

5. **`trusted_domain_peers` static in Rego** for Phases 3–5. Replaced by `domain_registry`
   DB table lookup in Phase 6. Guarded by a feature-flag comment in Rego.

6. **Workflow tokens are opaque** (32-byte random, base64url-encoded). SHA-256 hash stored
   in DB, raw token returned only at creation time — same pattern as SPIRE IAT handling.

7. **`DomainConfig` loaded from env vars:** `DOMAIN_{UPPER_NAME}_{FIELD}`
   (e.g. `DOMAIN_SUBSCRIPTION_KEYCLOAK_URL`). Enables per-domain config without code changes.

8. **`max_routing_hops` raised 5 → 8.** A full cross-domain chain consumes 5–6 hops.
   At 8 hops there is headroom for one level of re-delegation within a domain. Exceeding
   the limit returns a structured error, not silent truncation.

9. **`AgentManager` module-level singleton.** Current per-request instantiation re-parses
   all YAML files on every request — measurably expensive at 16 agents.

10. **RAG skipped for orchestrators.** Agents with `role: orchestrator` or `role: router`
    have `rag_enabled: false` implicitly — enforced in `main.py` before the RAG call.

---

## Cross-Cutting Rules

These apply to every phase and every PR:

### 1. The Three-Source Consistency Rule
Every new agent YAML must be reflected simultaneously in:
1. `agent-service/config/agents/{name}.yaml`
2. `agent_capabilities` map in `policies/agent_permissions.rego`
3. SPIRE workload entry in `scripts/seed/seed-services.sh`

A CI check or `scripts/sync_agent_capabilities.py` must validate these three are consistent.
Add this check to the PR validation pipeline before Phase 5 merges.

### 2. Act Chain Integrity
The RFC 8693 `act` claim chain must be preserved intact across every phase.
Each phase that touches token exchange or adds a new hop type must include an
integration test in `request-manager/tests/test_token_exchange_delegation.py`
asserting the act chain has the correct depth and contains the correct `sub` values.

### 3. Correlation ID Propagation
`correlation_id` must be propagated in `X-Correlation-ID` HTTP header across every
cross-domain hop. Extracted in `IdentityMiddleware`, logged at every service, stored
in `cross_domain_audit_events`. Required for distributed trace reconstruction.

### 4. Seed Script Idempotency
All seed script changes must be idempotent — running `seed-services.sh` twice must not
create duplicate SPIRE entries, Keycloak clients, or `domain_registry` rows.
`seed-domain-registry.py` must use `INSERT ... ON CONFLICT DO UPDATE`.

### 5. OPA Latency Budget
Run `opa bench` after Phase 3 to confirm p99 latency stays under 10ms with 7 Rego files.
If it exceeds this, consider `opa build --partial` for the most frequently evaluated rules.

### 6. Security Boundary: No Specialist-to-Specialist Cross-Domain Calls
Cross-domain calls must always follow:
```
specialist-A → orchestrator-A → cross-domain hop → orchestrator-B → specialist-B
```
OPA Rule 8 in Phase 3 enforces that only orchestrators (not specialists) can initiate
cross-domain calls. Verify this boundary with an integration test that asserts a direct
specialist→orchestrator cross-domain call is denied.

### 7. Structured Logging
Every new class in Phases 1–7 must use `configure_logging(SERVICE_NAME)` from
`shared_models`, not the stdlib `logging` module.

### 8. Seed Persistence (existing project rule)
Every configuration change made interactively must also be persisted in the seed scripts
before the PR is merged. Applies to every new agent YAML, Keycloak role, SPIRE entry,
and OPA policy file. Each phase PR checklist must include a verification step.

---

## Token Expiry Strategy Reference

The long-running workflow problem (discussed during Phase 2 architecture review):

| Strategy | Covers | Implemented |
|---|---|---|
| **1 — Refresh token** | Sync workflows < 30 min | Current (Token₀ TTL = 1800s) |
| **2 — Workflow token** | Async workflows < 8h | Phase 6 |
| **3 — Direct impersonation** | Workflows > 8h, caller completely disconnected | Deferred — requires `admin-fine-grained-authz` Keycloak build flag |

---

## Phase Dependency Graph

```
Phase 1 (YAML schema)
    └── Phase 2 (domain dispatch + MAO topology)
            ├── Phase 3 (OPA cross-domain policy)
            │       └── Phase 4 (cross-domain token exchange)
            │               └── Phase 6 (async workflows + DB)
            └── Phase 5 (new domain implementations)
                    └── Phase 6 (domain_registry seeding)
                            └── Phase 7 (seed scripts + infra for all 4 domains)
```

Phases 3 and 5 can run in parallel after Phase 2.
Phase 4 depends on both Phase 2 and Phase 3.
Phase 7 is the integration milestone — all prior phases must be deployed and passing tests.

---

*Generated from codebase analysis of 6 dimensions: routing, agents, token exchange, OPA, seed scripts, database.*
*Synthesized by multi-agent workflow on 2026-09-14.*
