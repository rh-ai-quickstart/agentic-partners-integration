# Scripts - Essential Setup & Testing

## Main Scripts

### `setup.sh` - Complete Setup
**One command to setup everything**

```bash
export GOOGLE_API_KEY="your-key"
bash scripts/setup.sh
```

**What it does:**
- Builds all container images
- Starts PostgreSQL, Keycloak, OPA
- Runs database migrations
- Starts agent-service, request-manager, rag-api, pf-chat-ui
- Ingests RAG knowledge base

Users are managed in Keycloak and auto-created in the DB on first login.

---

### `test.sh` - Complete Testing
**One command to test everything**

```bash
bash scripts/test.sh
```

**What it tests:**
- Health checks (all services including Keycloak, OPA)
- Keycloak authentication (login, token validation, invalid password rejection)
- OPA authorization (department-based agent access)
- RAG queries
- End-to-end workflow (login -> chat -> response)
- Database state

---

### `build_containers.sh`
Builds all container images (used by setup.sh)

---

## Quick Reference

```bash
# Complete setup + initialization
bash scripts/setup.sh

# Test everything
bash scripts/test.sh

# Just build containers
bash scripts/build_containers.sh
```
