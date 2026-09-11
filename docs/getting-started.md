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

## Test Users (local password login)

These accounts are always created regardless of which social providers are enabled.  They work in parallel with social login — both are available on the same Keycloak sign-in page.

| User | Password | Departments | Access |
|------|----------|-------------|--------|
| carlos@example.com | carlos123 | engineering, software, kubernetes | Software + Kubernetes support |
| luis@example.com | luis123 | engineering, network | Network support only |
| sharon@example.com | sharon123 | engineering, software, network, kubernetes, admin | All agents |
| josh@example.com | josh123 | _(none)_ | No agents (restricted) |

## Social Login (Google, GitHub, Microsoft)

You can enable any combination of Google/Gmail, GitHub, and Microsoft OAuth alongside the local password accounts — they are **additive**, not mutually exclusive.  Users who sign in with a social provider are matched to existing accounts by email, so the same department assignments apply.

**Quick setup — add credentials to `.env`:**

```bash
# Google / Gmail
GOOGLE_CLIENT_ID=your-google-client-id
GOOGLE_CLIENT_SECRET=your-google-client-secret

# GitHub
GITHUB_CLIENT_ID=your-github-oauth-app-client-id
GITHUB_CLIENT_SECRET=your-github-oauth-app-client-secret

# Microsoft (Entra ID / Azure AD)
MICROSOFT_CLIENT_ID=your-azure-app-registration-client-id
MICROSOFT_CLIENT_SECRET=your-azure-client-secret
MICROSOFT_TENANT=common        # or your tenant ID to restrict to one org
```

Then run `make setup` (or just `bash scripts/seed/seed-keycloak.sh`).  Any provider whose env vars are set gets a "Login with …" button on the Keycloak sign-in page; the others are silently skipped.

**How email-to-permission mapping works:**

1. User clicks "Login with Google" (or GitHub / Microsoft).
2. The external provider authenticates them and returns a verified email address.
3. Keycloak looks for an existing local user with that email.
   - **Match found** → the external identity is linked to that account; the user inherits whatever groups/departments are already assigned (e.g. if `carlos@gmail.com` is pre-seeded with `engineering, software, kubernetes`, they get those departments on first social login).
   - **No match** → Keycloak creates a new local user.  The request-manager upserts that email into PostgreSQL with **empty departments**.  The user can authenticate successfully but OPA blocks all agents until an admin assigns their departments (via the Keycloak admin console or directly in the DB).

**Redirect URIs to register with each provider (localhost defaults):**

| Provider | Callback URL |
|----------|-------------|
| Google | `http://localhost:8090/realms/partner-agent/broker/google/endpoint` |
| GitHub | `http://localhost:8090/realms/partner-agent/broker/github/endpoint` |
| Microsoft | `http://localhost:8090/realms/partner-agent/broker/microsoft/endpoint` |

For production deployments replace `http://localhost:8090` with your Keycloak public URL.

**Pre-seeding real emails with departments:**

To give a social-login user their departments from the very first login, create a local Keycloak account for them **before** they log in via the social provider (Keycloak will automatically link the social identity to the pre-existing account on first login):

```bash
# Example: give your real Google account the same access as "sharon"
KEYCLOAK_URL=http://localhost:8090
REALM=partner-agent
TOKEN=$(curl -sf -X POST "$KEYCLOAK_URL/realms/master/protocol/openid-connect/token" \
  -d "client_id=admin-cli&grant_type=password&username=admin&password=admin123" \
  | jq -r '.access_token')

# 1. Create the local user (no password needed — social provider handles auth)
USER_ID=$(curl -sf -X POST "$KEYCLOAK_URL/admin/realms/$REALM/users" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"username":"you@gmail.com","email":"you@gmail.com","emailVerified":true,"enabled":true}' \
  -D - | grep -i "^Location:" | sed 's/.*\///' | tr -d '\r\n')

# 2. Assign groups (departments)
for GROUP_ID in $(curl -sf "$KEYCLOAK_URL/admin/realms/$REALM/groups" \
  -H "Authorization: Bearer $TOKEN" | jq -r '.[] | select(.name=="engineering" or .name=="software" or .name=="kubernetes") | .id'); do
  curl -sf -X PUT "$KEYCLOAK_URL/admin/realms/$REALM/users/$USER_ID/groups/$GROUP_ID" \
    -H "Authorization: Bearer $TOKEN"
done
echo "Done — log in with your Google account now"
```

## Try It

1. Open http://localhost:3000
2. Click **Carlos** (or enter `carlos@example.com` / `carlos123`) and sign in
3. Type: "My app crashes with error 500" -- Routes to software-support agent (local) with RAG context
4. Type: "My pod is in CrashLoopBackOff" -- Routes to kubernetes-support agent (remote) with RAG context
5. Type: "VPN not connecting" -- Denied (Carlos lacks the `network` department)
6. Log out, sign in as `sharon@example.com` / `sharon123` -- All queries work (has all departments)
7. _(if configured)_ Log out, click "Login with Google" / "Login with GitHub" / "Login with Microsoft"

## Run Tests

```bash
make test   # E2E tests covering all four pillars
```

## Next Steps

- [Architecture Overview](architecture.md) -- system diagram, request flow, design decisions
- [Security (AAA)](aaa-security.md) -- authentication, authorization, audit trail
- [Development Guide](development.md) -- Makefile targets, building, testing
- [Configuration Reference](configuration.md) -- environment variables, LLM backends
