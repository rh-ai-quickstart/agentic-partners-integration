# PatternFly Chat UI

Web interface for interacting with partner agents, built with PatternFly components.

## Overview

- **Authentication** - Keycloak OIDC (login form -> Keycloak token -> JWT validation)
- **Authorization** - Department-based agent access via OPA
- **Conversation History** - Session management and context
- **Multi-Agent Support** - Routing to appropriate specialist agents

## Architecture

```
User -> PF Chat UI -> Request Manager -> Keycloak (auth) + OPA (authz) -> Routing Agent -> Partner Agents
                           |
                      PostgreSQL (Users, Sessions)
```

## Pages

| Page | URL | Description |
|------|-----|-------------|
| `login.html` | `/login.html` | Login form with test user buttons |
| `chat.html` | `/chat.html` | Main chat interface |
| `audit.html` | `/audit.html` | Request audit log |

## Authentication Flow

1. User enters email + password on login page (or clicks a test user button)
2. UI calls `POST /auth/login` with credentials
3. Request Manager authenticates against Keycloak (Resource Owner Password Grant)
4. Keycloak returns a signed JWT (RS256)
5. UI stores token in `localStorage` and sends it as `Authorization: Bearer` on all requests
6. Request Manager validates JWT via Keycloak's JWKS endpoint

## Department-Based Authorization

Agent access is determined by the intersection of user departments and agent capabilities, evaluated by OPA:

```
Effective Access = User Departments ∩ Agent Capabilities
```

| User | Departments | Accessible Agents |
|------|------------|-------------------|
| Carlos (carlos@example.com) | engineering, software | software-support |
| Luis (luis@example.com) | engineering, network | network-support |
| Sharon (sharon@example.com) | engineering, software, network, admin | all agents |
| Josh (josh@example.com) | (none) | no agents |

Departments come from Keycloak realm roles in the JWT's `realm_access.roles` claim.

## Deployment

The UI is served by nginx as a static site:

```yaml
pf-chat-ui:
  image: nginx:alpine
  volumes:
    - ./pf-chat-ui/static:/usr/share/nginx/html:ro
    - ./pf-chat-ui/nginx.conf:/etc/nginx/nginx.conf:ro
  ports:
    - "3000:80"
```

## API Endpoints Used

### POST /auth/login
```json
{ "email": "carlos@example.com", "password": "carlos123" }
```
Returns: `{ "token": "eyJ...", "user": { "email", "role", "departments" } }`

### GET /auth/me
Headers: `Authorization: Bearer <token>`
Returns: `{ "email", "role", "departments" }`

### POST /adk/chat
Headers: `Authorization: Bearer <token>`
```json
{ "message": "My app crashes", "user": { "email": "carlos@example.com" } }
```
Returns: `{ "response", "session_id", "agent", "user_context" }`

### GET /adk/audit
Headers: `Authorization: Bearer <token>`
Returns: `{ "entries", "total", "user_email", "user_role" }`

For more information, see the main [README](../README.md).
