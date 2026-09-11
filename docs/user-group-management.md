# User and Group Management

## Overview

This document explains where user/group data lives, how it flows through the system, and how to manage it in production.

## 🗺️ **Architecture: Where Data Lives**

```
┌──────────────────────────────────────────────────────────────────┐
│ 1. SOURCE OF TRUTH: Keycloak                                    │
│    (keycloak/realm-partner.json)                                 │
│                                                                  │
│    Users:                                                        │
│      • carlos@example.com → groups: [engineering, software, k8s]│
│      • luis@example.com → groups: [engineering, network]        │
│      • sharon@example.com → groups: [all departments]           │
│      • josh@example.com → groups: []                            │
│                                                                  │
│    Groups:                                                       │
│      • /engineering                                             │
│      • /software                                                │
│      • /network                                                 │
│      • /kubernetes                                              │
│      • /admin                                                   │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 2. TRANSPORT: JWT Tokens                                        │
│    (Issued by Keycloak on user login)                           │
│                                                                  │
│    JWT Claims:                                                   │
│      {                                                          │
│        "sub": "user-uuid",                                      │
│        "email": "carlos@example.com",                           │
│        "groups": ["engineering", "software", "kubernetes"],  ← │
│        "iat": 1234567890,                                       │
│        "exp": 1234569690                                        │
│      }                                                          │
│                                                                  │
│    Protocol Mapper: oidc-group-membership-mapper                │
│      • Configured in: keycloak/realm-partner.json               │
│      • Claim name: "groups"                                     │
│      • Full path: false (no leading /)                          │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 3. STORAGE: PostgreSQL Database                                 │
│    (Optional caching - NOT the source of truth)                 │
│                                                                  │
│    users table:                                                  │
│      • id (UUID)                                                │
│      • primary_email (string)                                   │
│      • role (enum: user, admin)                                 │
│      • departments (jsonb) ← Synced from JWT on first login    │
│      • organization, status, etc.                               │
│                                                                  │
│    Code: shared-models/src/shared_models/models.py              │
└──────────────────────────────────────────────────────────────────┘
                              ↓
┌──────────────────────────────────────────────────────────────────┐
│ 4. FALLBACK: OPA Static Map                                     │
│    (Only used if JWT has no groups AND DB has no record)        │
│                                                                  │
│    policies/user_permissions.rego:                              │
│      user_departments_fallback := {                             │
│        "carlos@example.com": ["engineering", "software", "k8s"],│
│        "luis@example.com": ["engineering", "network"],          │
│        "sharon@example.com": ["all"],                           │
│        "josh@example.com": []                                   │
│      }                                                          │
│                                                                  │
│    ⚠️ This is ONLY for local development/testing               │
└──────────────────────────────────────────────────────────────────┘
```

## 🔄 **Data Flow: Login to Authorization**

### Step 1: User Login

```
User → Web UI (http://localhost:3000)
  ↓
Web UI → Keycloak (http://localhost:8090)
  Login form: carlos@example.com / carlos123
  ↓
Keycloak validates credentials
  ↓
Keycloak generates JWT token with claims:
  {
    "email": "carlos@example.com",
    "groups": ["engineering", "software", "kubernetes"]  ← From realm config
  }
  ↓
Web UI receives JWT token
```

**Where groups come from:**
- Keycloak reads: `keycloak/realm-partner.json` → `users[].groups`
- Protocol mapper: `oidc-group-membership-mapper` adds `groups` claim to JWT

### Step 2: API Request

```
Web UI → Request Manager (http://localhost:8000)
  Headers:
    Authorization: Bearer <JWT-token>
  ↓
Request Manager validates JWT:
  1. Verify signature (Keycloak public key)
  2. Check expiration
  3. Extract claims (email, groups)
  ↓
Create/update user in database:
  Code: request-manager/src/request_manager/auth_endpoints.py
  
  user = await AAAService.get_or_create_user(
      db,
      email=jwt_claims["email"],
      departments=jwt_claims.get("groups", [])  ← Store in DB
  )
```

**Where groups go:**
- Extracted from JWT `groups` claim
- Stored in PostgreSQL `users.departments` (jsonb array)
- Used for all subsequent requests in this session

### Step 3: Authorization Decision

```
Request Manager → OPA (http://localhost:8181)
  POST /v1/data/partner/authorization/decision
  {
    "input": {
      "caller_spiffe_id": "spiffe://partner.example.com/request-manager",
      "agent_name": "kubernetes-support",
      "delegation": {
        "user_spiffe_id": "spiffe://partner.example.com/user/carlos",
        "user_departments": ["engineering", "software", "kubernetes"]  ← From DB/JWT
      }
    }
  }
  ↓
OPA evaluates policies:
  policies/delegation.rego:
    - Get user departments (from input.delegation.user_departments)
    - Get agent capabilities (from policies/agent_permissions.rego)
    - Compute intersection: ["kubernetes"] ∩ ["kubernetes"] = ["kubernetes"]
    - Allow: true ✅
  ↓
OPA returns decision:
  {
    "result": {
      "allow": true,
      "reason": "Delegated access granted — effective departments: ['kubernetes']",
      "effective_departments": ["kubernetes"]
    }
  }
```

**Where OPA gets departments:**
1. **Primary:** From `input.delegation.user_departments` (passed by Request Manager from DB/JWT)
2. **Fallback:** If not in input, looks up in `user_departments_fallback` static map (OPA policy)

## 📝 **How to Manage Users and Groups**

### Development (Local Testing)

**Option 1: Modify Keycloak Realm Config** (Recommended)

```bash
# Edit keycloak/realm-partner.json
vim keycloak/realm-partner.json

# Add a new user:
{
  "users": [
    {
      "username": "alice",
      "email": "alice@example.com",
      "credentials": [{"type": "password", "value": "alice123"}],
      "realmRoles": ["engineering", "software"],
      "groups": ["/engineering", "/software"]
    }
  ]
}

# Restart Keycloak to reload realm:
docker restart partner-keycloak

# Wait for Keycloak to be ready:
sleep 30

# Test login:
curl -X POST http://localhost:8090/realms/partner-agent/protocol/openid-connect/token \
  -d "client_id=partner-agent-ui" \
  -d "grant_type=password" \
  -d "username=alice" \
  -d "password=alice123"
```

**Option 2: Keycloak Admin UI**

```bash
# Open Keycloak Admin Console:
http://localhost:8090

# Login: admin / admin123

# Navigate to:
#   1. Realms → partner-agent
#   2. Users → Add User
#   3. Fill form:
#      - Username: alice
#      - Email: alice@example.com
#      - Email Verified: ON
#   4. Click "Create"
#   5. Go to "Credentials" tab → Set Password
#   6. Go to "Groups" tab → Join groups (/engineering, /software)
#   7. Go to "Role Mappings" tab → Assign roles (engineering, software)

# Test login immediately (no restart needed)
```

**Option 3: Update OPA Fallback** (Quick test only)

```rego
# Edit policies/user_permissions.rego

user_departments_fallback := {
    "carlos@example.com": ["engineering", "software", "kubernetes"],
    "luis@example.com": ["engineering", "network"],
    "sharon@example.com": ["engineering", "software", "network", "kubernetes", "admin"],
    "josh@example.com": [],
    "alice@example.com": ["engineering", "software"]  # ← Add here
}

# No restart needed - OPA reloads policies automatically
```

⚠️ **Warning:** This only works if JWT doesn't have groups claim. Not recommended for real testing.

### Production (External Identity Provider)

**Option 1: LDAP/Active Directory Sync**

```yaml
# In Keycloak Admin UI:
# User Federation → Add Provider → LDAP

Connection Settings:
  Edit Mode: READ_ONLY
  Vendor: Active Directory
  Connection URL: ldap://ad.example.com:389
  Bind DN: cn=admin,dc=example,dc=com
  Bind Credential: <password>

LDAP Searching:
  Users DN: ou=users,dc=example,dc=com
  User Object Classes: person, organizationalPerson, user
  Username LDAP attribute: sAMAccountName
  Email LDAP attribute: mail

LDAP Group Membership:
  Membership Attribute: memberOf
  Group Name LDAP Attribute: cn
  Group Path: ou=groups,dc=example,dc=com

Sync Settings:
  Sync Registrations: ON
  Import Users: ON
  Batch Size: 1000
  Periodic Full Sync: Enabled (every 24h)
  Periodic Changed Users Sync: Enabled (every 1h)
```

**Option 2: SAML/OIDC Federation**

```yaml
# In Keycloak Admin UI:
# Identity Providers → Add Provider → SAML 2.0 / OIDC

OIDC Example (Okta, Auth0, Azure AD):
  Alias: azure-ad
  Authorization URL: https://login.microsoftonline.com/<tenant>/oauth2/v2.0/authorize
  Token URL: https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token
  Client ID: <app-id>
  Client Secret: <secret>

Mappers:
  - Name: groups
    Mapper Type: Attribute Importer
    Claim: groups
    User Attribute Name: departments
```

**Option 3: Keycloak REST API** (Automation)

```python
# Script to bulk import users from CSV
import requests

KEYCLOAK_URL = "http://localhost:8090"
REALM = "partner-agent"
ADMIN_USER = "admin"
ADMIN_PASS = "admin123"

# 1. Get admin token
token_resp = requests.post(
    f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token",
    data={
        "client_id": "admin-cli",
        "grant_type": "password",
        "username": ADMIN_USER,
        "password": ADMIN_PASS
    }
)
admin_token = token_resp.json()["access_token"]

# 2. Create user
create_resp = requests.post(
    f"{KEYCLOAK_URL}/admin/realms/{REALM}/users",
    headers={"Authorization": f"Bearer {admin_token}"},
    json={
        "username": "alice",
        "email": "alice@example.com",
        "emailVerified": True,
        "enabled": True,
        "credentials": [{"type": "password", "value": "alice123", "temporary": False}]
    }
)
user_id = create_resp.headers["Location"].split("/")[-1]

# 3. Assign groups
requests.put(
    f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}/groups/{engineering_group_id}",
    headers={"Authorization": f"Bearer {admin_token}"}
)

# 4. Assign roles
requests.post(
    f"{KEYCLOAK_URL}/admin/realms/{REALM}/users/{user_id}/role-mappings/realm",
    headers={"Authorization": f"Bearer {admin_token}"},
    json=[
        {"id": "<engineering-role-id>", "name": "engineering"},
        {"id": "<software-role-id>", "name": "software"}
    ]
)
```

## 🔍 **Debugging: Where to Look**

### Check JWT Token Claims

```bash
# Get JWT token:
TOKEN=$(curl -s -X POST http://localhost:8090/realms/partner-agent/protocol/openid-connect/token \
  -d "client_id=partner-agent-ui" \
  -d "grant_type=password" \
  -d "username=carlos" \
  -d "password=carlos123" | jq -r .access_token)

# Decode JWT (header.payload.signature):
echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | jq

# Expected output:
{
  "exp": 1234569690,
  "iat": 1234567890,
  "sub": "user-uuid",
  "email": "carlos@example.com",
  "groups": ["engineering", "software", "kubernetes"],  ← HERE
  "preferred_username": "carlos"
}
```

### Check Database User Record

```bash
# Connect to PostgreSQL:
docker exec -it partner-postgres-adk psql -U user -d partner_agent

# Check user departments:
SELECT primary_email, role, departments, organization 
FROM users 
WHERE primary_email = 'carlos@example.com';

#           primary_email           | role |            departments            | organization 
# ----------------------------------+------+-----------------------------------+--------------
#  carlos@example.com               | user | ["engineering","software","k8s"]  | example.com
```

### Check OPA Decision

```bash
# Test OPA policy directly:
curl -X POST http://localhost:8181/v1/data/partner/authorization/decision \
  -H "Content-Type: application/json" \
  -d '{
    "input": {
      "caller_spiffe_id": "spiffe://partner.example.com/request-manager",
      "agent_name": "kubernetes-support",
      "delegation": {
        "user_spiffe_id": "spiffe://partner.example.com/user/carlos",
        "user_departments": ["engineering", "software", "kubernetes"]
      }
    }
  }' | jq

# Expected output:
{
  "result": {
    "allow": true,
    "reason": "Delegated access granted — effective departments: ['kubernetes']",
    "effective_departments": ["kubernetes"]
  }
}
```

### Check Keycloak Group Mappings

```bash
# List all users in Keycloak:
curl -X GET http://localhost:8090/admin/realms/partner-agent/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.[] | {username, email, groups}'

# Get specific user:
curl -X GET http://localhost:8090/admin/realms/partner-agent/users?email=carlos@example.com \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq

# Get user's groups:
curl -X GET http://localhost:8090/admin/realms/partner-agent/users/$USER_ID/groups \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq
```

## 🏗️ **Architecture Decisions**

### Why Multiple Layers?

1. **Keycloak** (Source of Truth)
   - ✅ Single source for user management
   - ✅ Integration with enterprise identity providers (LDAP, SAML, OIDC)
   - ✅ JWT tokens are stateless and portable
   - ✅ Group changes reflect immediately on next login

2. **PostgreSQL** (Performance Cache)
   - ✅ Fast lookups without Keycloak round-trip
   - ✅ Supports offline/degraded Keycloak scenarios
   - ✅ Audit trail (user activity history)
   - ⚠️ Can be stale if Keycloak groups change

3. **OPA Static Map** (Development Fallback)
   - ✅ Works without Keycloak running
   - ✅ Useful for unit tests
   - ⚠️ Must be manually synced
   - ⚠️ Only for local development

### Sync Strategy

**Recommended: JWT is authoritative**

On every request:
1. Extract `groups` from JWT
2. Update PostgreSQL `users.departments` if different
3. Pass to OPA for authorization

This ensures:
- ✅ Groups are always fresh (from Keycloak)
- ✅ Database reflects latest state
- ✅ No manual sync required

**Code location:** `request-manager/src/request_manager/auth_endpoints.py`

```python
# On login callback:
jwt_claims = decode_jwt(token)
user = await AAAService.get_or_create_user(
    db,
    email=jwt_claims["email"],
    departments=jwt_claims.get("groups", [])  # ← Sync from JWT
)
```

## 🎯 **Summary**

```
Data Flow:
  Keycloak (realm-partner.json)
    ↓
  JWT Token (groups claim)
    ↓
  PostgreSQL (users.departments)
    ↓
  OPA (authorization decision)
    ↓ (fallback)
  OPA Static Map (user_permissions.rego)

Management:
  • Development: Edit realm-partner.json OR use Keycloak UI
  • Production: LDAP sync, SAML/OIDC federation, OR Keycloak REST API
  • Testing: OPA static map (fallback only)

Source of Truth: Keycloak
Cache: PostgreSQL
Fallback: OPA policies (dev/test only)
```

For production deployments, always use Keycloak with enterprise identity provider integration.
