#!/bin/bash
# Seed Keycloak with initial users and groups via REST API
# This runs after Keycloak starts and creates the realm

set -e

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8090}"
REALM="${KEYCLOAK_REALM:-partner-agent}"
ADMIN_USER="${KEYCLOAK_ADMIN:-admin}"
ADMIN_PASS="${KEYCLOAK_ADMIN_PASSWORD:-admin123}"

echo "════════════════════════════════════════════════════════════"
echo "Seeding Keycloak with initial data"
echo "════════════════════════════════════════════════════════════"
echo ""

# Wait for Keycloak to be ready
echo "Waiting for Keycloak to be ready..."
for i in {1..60}; do
    if curl -sf "${KEYCLOAK_URL}/realms/master" > /dev/null 2>&1; then
        echo "OK Keycloak is ready"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "ERROR Keycloak failed to become ready"
        exit 1
    fi
    sleep 2
done

# Get admin token
echo ""
echo "Getting admin access token..."
ADMIN_TOKEN=$(curl -sf -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "client_id=admin-cli" \
    -d "grant_type=password" \
    -d "username=${ADMIN_USER}" \
    -d "password=${ADMIN_PASS}" | jq -r '.access_token')

if [ -z "$ADMIN_TOKEN" ] || [ "$ADMIN_TOKEN" = "null" ]; then
    echo "ERROR Failed to get admin token"
    exit 1
fi
echo "OK Got admin token"

# Check if realm exists
echo ""
echo "Checking if realm '${REALM}' exists..."
REALM_EXISTS=$(curl -sf -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" > /dev/null 2>&1 && echo "yes" || echo "no")

if [ "$REALM_EXISTS" = "no" ]; then
    echo "Creating realm '${REALM}'..."
    curl -sf -X POST "${KEYCLOAK_URL}/admin/realms" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
            "realm": "'${REALM}'",
            "enabled": true,
            "displayName": "Partner Agent System",
            "sslRequired": "none",
            "registrationAllowed": false,
            "loginWithEmailAllowed": true,
            "duplicateEmailsAllowed": false,
            "resetPasswordAllowed": false,
            "editUsernameAllowed": false,
            "bruteForceProtected": true,
            "accessTokenLifespan": 1800,
            "ssoSessionIdleTimeout": 3600,
            "ssoSessionMaxLifespan": 36000
        }'
    echo "OK Realm created"

    # Refresh token for new realm
    sleep 2
    ADMIN_TOKEN=$(curl -sf -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=admin-cli" \
        -d "grant_type=password" \
        -d "username=${ADMIN_USER}" \
        -d "password=${ADMIN_PASS}" | jq -r '.access_token')
else
    echo "OK Realm exists"
fi

# Create roles
echo ""
echo "Creating realm roles..."
for role in engineering software network kubernetes admin; do
    ROLE_EXISTS=$(curl -sf -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}/roles/${role}" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" > /dev/null 2>&1 && echo "yes" || echo "no")

    if [ "$ROLE_EXISTS" = "no" ]; then
        curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/roles" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            -d '{
                "name": "'${role}'",
                "description": "'${role^}' department role"
            }'
        echo "  OK Created role: ${role}"
    else
        echo "  - Role exists: ${role}"
    fi
done

# Create groups
echo ""
echo "Creating groups..."
declare -A GROUP_IDS
for group in engineering software network kubernetes admin; do
    GROUP_EXISTS=$(curl -sf -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}/groups?search=${group}&exact=true" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq -r '.[0].id // empty')

    if [ -z "$GROUP_EXISTS" ]; then
        RESPONSE=$(curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/groups" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            -d '{
                "name": "'${group}'",
                "path": "/'${group}'"
            }' -D -)

        # Extract group ID from Location header
        GROUP_ID=$(echo "$RESPONSE" | grep -i "^Location:" | sed 's/.*\///' | tr -d '\r\n')
        GROUP_IDS[$group]=$GROUP_ID
        echo "  OK Created group: ${group} (${GROUP_ID})"

        # Assign role to group
        ROLE_ID=$(curl -sf -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}/roles/${group}" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq -r '.id')

        if [ -n "$ROLE_ID" ] && [ "$ROLE_ID" != "null" ]; then
            curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/groups/${GROUP_ID}/role-mappings/realm" \
                -H "Authorization: Bearer ${ADMIN_TOKEN}" \
                -H "Content-Type: application/json" \
                -d '[{"id": "'${ROLE_ID}'", "name": "'${group}'"}]'
        fi
    else
        GROUP_IDS[$group]=$GROUP_EXISTS
        echo "  - Group exists: ${group} (${GROUP_EXISTS})"
    fi
done

# Create OIDC client with group mapper
echo ""
echo "Creating OIDC client..."
CLIENT_EXISTS=$(curl -sf -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}/clients?clientId=partner-agent-ui" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq -r '.[0].id // empty')

if [ -z "$CLIENT_EXISTS" ]; then
    CLIENT_RESPONSE=$(curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/clients" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
            "clientId": "partner-agent-ui",
            "name": "Partner Agent UI",
            "enabled": true,
            "publicClient": true,
            "directAccessGrantsEnabled": true,
            "standardFlowEnabled": true,
            "implicitFlowEnabled": false,
            "redirectUris": ["http://localhost:3000/*"],
            "webOrigins": ["http://localhost:3000"],
            "protocol": "openid-connect"
        }' -D -)

    CLIENT_ID=$(echo "$CLIENT_RESPONSE" | grep -i "^Location:" | sed 's/.*\///' | tr -d '\r\n')
    echo "  OK Created client: partner-agent-ui (${CLIENT_ID})"

    # Add group membership mapper
    sleep 1
    curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_ID}/protocol-mappers/models" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{
            "name": "groups",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-group-membership-mapper",
            "consentRequired": false,
            "config": {
                "full.path": "false",
                "introspection.token.claim": "true",
                "userinfo.token.claim": "true",
                "multivalued": "true",
                "id.token.claim": "true",
                "access.token.claim": "true",
                "claim.name": "groups"
            }
        }'
    echo "  OK Added group mapper to client"
else
    echo "  - Client exists: partner-agent-ui"
fi

# ---------------------------------------------------------------------------
# Social login identity providers (Google, GitHub, Microsoft)
# All three are optional.  Each is skipped when its env vars are not set.
# When credentials ARE present the provider is created (or left untouched if
# it already exists) and two mappers are attached:
#   1. email  – maps the external email claim onto the Keycloak user email
#   2. username – maps the external sub/login to a stable username
#
# "trustEmail: true" means Keycloak accepts the email as already-verified by
# the external provider.  This is safe for Google (verified by Google),
# GitHub (scope user:email returns only verified addresses), and Microsoft
# (verified by Entra ID).
#
# First-time social login: Keycloak looks for an existing local user whose
# email matches.  If found it links the external identity to that account
# (the user keeps the groups/departments already assigned in the DB).
# If no match exists, Keycloak creates a new local user.  The request-manager
# upserts that email into PostgreSQL with empty departments — the user can
# log in but OPA blocks all agents until an admin assigns departments.
# ---------------------------------------------------------------------------

# Helper: create an identity provider only when it doesn't exist yet
create_idp() {
    local alias=$1
    local provider_id=$2
    local body=$3

    IDP_EXISTS=$(curl -sf -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}/identity-provider/instances/${alias}" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" > /dev/null 2>&1 && echo "yes" || echo "no")

    if [ "$IDP_EXISTS" = "no" ]; then
        curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/identity-provider/instances" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "${body}"
        echo "  OK Created identity provider: ${alias}"
    else
        echo "  - Identity provider exists: ${alias}"
    fi
}

# Helper: add a mapper to an identity provider only when it doesn't exist yet
add_idp_mapper() {
    local alias=$1
    local mapper_name=$2
    local body=$3

    MAPPER_EXISTS=$(curl -sf -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}/identity-provider/instances/${alias}/mappers" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq -r --arg n "$mapper_name" '.[] | select(.name==$n) | .id // empty')

    if [ -z "$MAPPER_EXISTS" ]; then
        curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/identity-provider/instances/${alias}/mappers" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "${body}"
        echo "    OK Mapper: ${mapper_name}"
    else
        echo "    - Mapper exists: ${mapper_name}"
    fi
}

# ── Google / Gmail ──────────────────────────────────────────────────────────
echo ""
echo "Configuring Google identity provider..."
if [ -n "${GOOGLE_CLIENT_ID}" ] && [ -n "${GOOGLE_CLIENT_SECRET}" ]; then
    create_idp "google" "google" '{
        "alias":       "google",
        "providerId":  "google",
        "enabled":     true,
        "trustEmail":  true,
        "storeToken":  false,
        "addReadTokenRoleOnCreate": false,
        "firstBrokerLoginFlowAlias": "first broker login",
        "config": {
            "clientId":     "'"${GOOGLE_CLIENT_ID}"'",
            "clientSecret": "'"${GOOGLE_CLIENT_SECRET}"'",
            "defaultScope": "openid email profile",
            "hostedDomain": ""
        }
    }'
    add_idp_mapper "google" "email" '{
        "name":             "email",
        "identityProviderAlias": "google",
        "identityProviderMapper": "oidc-user-attribute-idp-mapper",
        "config": {
            "syncMode":          "INHERIT",
            "claim":             "email",
            "user.attribute":    "email"
        }
    }'
    add_idp_mapper "google" "username" '{
        "name":             "username",
        "identityProviderAlias": "google",
        "identityProviderMapper": "oidc-username-idp-mapper",
        "config": {
            "syncMode":  "INHERIT",
            "template":  "${CLAIM.email}"
        }
    }'
else
    echo "  - Skipped (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set)"
fi

# ── GitHub ──────────────────────────────────────────────────────────────────
echo ""
echo "Configuring GitHub identity provider..."
if [ -n "${GITHUB_CLIENT_ID}" ] && [ -n "${GITHUB_CLIENT_SECRET}" ]; then
    create_idp "github" "github" '{
        "alias":       "github",
        "providerId":  "github",
        "enabled":     true,
        "trustEmail":  true,
        "storeToken":  false,
        "addReadTokenRoleOnCreate": false,
        "firstBrokerLoginFlowAlias": "first broker login",
        "config": {
            "clientId":     "'"${GITHUB_CLIENT_ID}"'",
            "clientSecret": "'"${GITHUB_CLIENT_SECRET}"'",
            "defaultScope": "user:email"
        }
    }'
    add_idp_mapper "github" "email" '{
        "name":             "email",
        "identityProviderAlias": "github",
        "identityProviderMapper": "hardcoded-attribute-idp-mapper",
        "config": {
            "syncMode":       "INHERIT",
            "attribute":      "email",
            "attribute.value": "${ATTRIBUTE.email}"
        }
    }'
    add_idp_mapper "github" "username" '{
        "name":             "username",
        "identityProviderAlias": "github",
        "identityProviderMapper": "github-user-attribute-mapper",
        "config": {
            "syncMode":        "INHERIT",
            "jsonField":       "login",
            "userAttribute":   "username"
        }
    }'
else
    echo "  - Skipped (GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET not set)"
fi

# ── Microsoft (Entra ID / Azure AD) ─────────────────────────────────────────
echo ""
echo "Configuring Microsoft identity provider..."
if [ -n "${MICROSOFT_CLIENT_ID}" ] && [ -n "${MICROSOFT_CLIENT_SECRET}" ]; then
    # MICROSOFT_TENANT defaults to "common" which accepts personal + work accounts.
    # Set MICROSOFT_TENANT to your organisation's tenant ID to restrict to
    # a single Azure AD tenant (recommended for production).
    MICROSOFT_TENANT="${MICROSOFT_TENANT:-common}"
    create_idp "microsoft" "microsoft" '{
        "alias":       "microsoft",
        "providerId":  "microsoft",
        "enabled":     true,
        "trustEmail":  true,
        "storeToken":  false,
        "addReadTokenRoleOnCreate": false,
        "firstBrokerLoginFlowAlias": "first broker login",
        "config": {
            "clientId":     "'"${MICROSOFT_CLIENT_ID}"'",
            "clientSecret": "'"${MICROSOFT_CLIENT_SECRET}"'",
            "defaultScope": "openid email profile",
            "tenantId":     "'"${MICROSOFT_TENANT}"'"
        }
    }'
    add_idp_mapper "microsoft" "email" '{
        "name":             "email",
        "identityProviderAlias": "microsoft",
        "identityProviderMapper": "oidc-user-attribute-idp-mapper",
        "config": {
            "syncMode":       "INHERIT",
            "claim":          "email",
            "user.attribute": "email"
        }
    }'
    add_idp_mapper "microsoft" "username" '{
        "name":             "username",
        "identityProviderAlias": "microsoft",
        "identityProviderMapper": "oidc-username-idp-mapper",
        "config": {
            "syncMode":  "INHERIT",
            "template":  "${CLAIM.email}"
        }
    }'
else
    echo "  - Skipped (MICROSOFT_CLIENT_ID / MICROSOFT_CLIENT_SECRET not set)"
fi

# ---------------------------------------------------------------------------
# Local users
# ---------------------------------------------------------------------------

# Create users
echo ""
echo "Creating users..."

# Function to create user
create_user() {
    local username=$1
    local email=$2
    local password=$3
    local first_name=$4
    local last_name=$5
    shift 5
    local groups=("$@")

    USER_EXISTS=$(curl -sf -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}/users?email=${email}&exact=true" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq -r '.[0].id // empty')

    if [ -z "$USER_EXISTS" ]; then
        USER_RESPONSE=$(curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/users" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            -d '{
                "username": "'${username}'",
                "email": "'${email}'",
                "firstName": "'${first_name}'",
                "lastName": "'${last_name}'",
                "emailVerified": true,
                "enabled": true
            }' -D -)

        USER_ID=$(echo "$USER_RESPONSE" | grep -i "^Location:" | sed 's/.*\///' | tr -d '\r\n')
        echo "  OK Created user: ${email} (${USER_ID})"

        # Set password
        sleep 0.5
        curl -sf -X PUT "${KEYCLOAK_URL}/admin/realms/${REALM}/users/${USER_ID}/reset-password" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            -d '{
                "type": "password",
                "value": "'${password}'",
                "temporary": false
            }'

        # Assign groups
        for group in "${groups[@]}"; do
            if [ -n "${GROUP_IDS[$group]}" ]; then
                curl -sf -X PUT "${KEYCLOAK_URL}/admin/realms/${REALM}/users/${USER_ID}/groups/${GROUP_IDS[$group]}" \
                    -H "Authorization: Bearer ${ADMIN_TOKEN}"
            fi
        done
        echo "    Groups: ${groups[*]}"
    else
        echo "  - User exists: ${email}"
    fi
}

# Seed users
create_user "carlos" "carlos@example.com" "carlos123" "Carlos" "Camacho" engineering software kubernetes
create_user "luis" "luis@example.com" "luis123" "Luis" "Arizmendi" engineering network
create_user "sharon" "sharon@example.com" "sharon123" "Sharon" "Admin" engineering software network kubernetes admin
create_user "josh" "josh@example.com" "josh123" "Josh" "Restricted" # No groups

# ---------------------------------------------------------------------------
# DCR — Dynamic Client Registration setup
#
# Creates an Initial Access Token (IAT) that agents use as the Authorization
# bearer when POSTing to the Keycloak DCR endpoint.  The IAT lets any
# service that holds it register itself as a Keycloak client without
# requiring pre-seeding.
#
# count=100  → up to 100 client registrations per IAT
# expiration=0 → IAT never expires (agents can restart freely)
#
# The token is written to /tmp/dcr_initial_access_token.txt so that
# seed-services.sh can read it and pass it to agent containers.
# ---------------------------------------------------------------------------
echo ""
echo "Configuring Dynamic Client Registration (DCR)..."

# Check if an IAT already exists (idempotent)
EXISTING_IAT_COUNT=$(curl -sf -X GET "${KEYCLOAK_URL}/admin/realms/${REALM}/clients-initial-access" \
    -H "Authorization: Bearer ${ADMIN_TOKEN}" | jq -r 'length' 2>/dev/null || echo "0")

if [ "${EXISTING_IAT_COUNT}" -gt "0" ]; then
    echo "  - DCR Initial Access Token already exists (count: ${EXISTING_IAT_COUNT})"
    # Re-use existing token — fetch the first one's token value
    # Note: Keycloak only returns the token value on creation, not on subsequent GETs.
    # If it was created in a previous run the seed script writes it to the tmp file.
    if [ -f /tmp/dcr_initial_access_token.txt ]; then
        DCR_IAT=$(cat /tmp/dcr_initial_access_token.txt)
        echo "  OK Using cached DCR IAT"
    else
        # Re-create (idempotent — old ones remain valid)
        DCR_IAT_RESPONSE=$(curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/clients-initial-access" \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            -d '{"count": 100, "expiration": 0}')
        DCR_IAT=$(echo "${DCR_IAT_RESPONSE}" | jq -r '.token // empty')
        [ -n "${DCR_IAT}" ] && echo "${DCR_IAT}" > /tmp/dcr_initial_access_token.txt
        echo "  OK Created new DCR IAT (existing ones still valid)"
    fi
else
    DCR_IAT_RESPONSE=$(curl -sf -X POST "${KEYCLOAK_URL}/admin/realms/${REALM}/clients-initial-access" \
        -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -H "Content-Type: application/json" \
        -d '{"count": 100, "expiration": 0}')
    DCR_IAT=$(echo "${DCR_IAT_RESPONSE}" | jq -r '.token // empty')

    if [ -z "${DCR_IAT}" ]; then
        echo "  ✗ Failed to create DCR Initial Access Token"
        echo "    Response: ${DCR_IAT_RESPONSE}"
    else
        echo "${DCR_IAT}" > /tmp/dcr_initial_access_token.txt
        echo "  OK Created DCR Initial Access Token"
        echo "  ↳ Saved to /tmp/dcr_initial_access_token.txt for seed-services.sh"
    fi
fi
export DCR_IAT

# ---------------------------------------------------------------------------
# Authorization Services for partner-agent-ui — enables DCR clients to
# perform RFC 8693 token exchange on behalf of the user.
#
# Keycloak token-exchange:v1 (legacy) requires the source client that issued
# the subject_token (partner-agent-ui) to have Authorization Services enabled
# with a token-exchange permission.  The permission is linked to a role policy
# requiring the realm-management/impersonation role, which is granted to every
# DCR client's service account by dcr_client.py._enable_legacy_token_exchange().
#
# This is a one-time seeding step; it is idempotent (scope/resource/policy/
# permission creation is skipped if the names already exist).
# ---------------------------------------------------------------------------
echo ""
echo "Configuring token-exchange fine-grained authorization on partner-agent-ui..."

PAGU_ID=$(curl -sf -H "Authorization: Bearer ${ADMIN_TOKEN}" \
    "${KEYCLOAK_URL}/admin/realms/${REALM}/clients?clientId=partner-agent-ui" | jq -r '.[0].id // empty')

if [ -z "$PAGU_ID" ]; then
    echo "  ✗ partner-agent-ui client not found — skipping authz setup"
else
    # Enable authorization services on partner-agent-ui (idempotent PUT)
    PAGU_CONFIG=$(curl -sf -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}")
    PAGU_UPDATED=$(echo "$PAGU_CONFIG" | jq '.authorizationServicesEnabled = true | .serviceAccountsEnabled = true')
    curl -sf -X PUT -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        -H "Content-Type: application/json" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}" \
        -d "$PAGU_UPDATED" >/dev/null 2>&1 || true

    # Create token-exchange scope (idempotent — skip if already exists)
    EXISTING_SCOPE=$(curl -sf -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}/authz/resource-server/scope?name=token-exchange" \
        | jq -r '.[0].id // empty' 2>/dev/null)
    if [ -z "$EXISTING_SCOPE" ]; then
        SCOPE_RESP=$(curl -sf -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}/authz/resource-server/scope" \
            -d '{"name": "token-exchange"}' 2>/dev/null || echo '{}')
        TE_SCOPE_ID=$(echo "$SCOPE_RESP" | jq -r '.id // empty')
        echo "  OK Created token-exchange scope (${TE_SCOPE_ID})"
    else
        TE_SCOPE_ID="$EXISTING_SCOPE"
        echo "  - token-exchange scope already exists"
    fi

    # Create client resource for token exchange (named after client ID)
    EXISTING_RESOURCE=$(curl -sf -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}/authz/resource-server/resource?name=client.resource.${PAGU_ID}" \
        | jq -r '.[0]._id // empty' 2>/dev/null)
    if [ -z "$EXISTING_RESOURCE" ]; then
        RESOURCE_RESP=$(curl -sf -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}/authz/resource-server/resource" \
            -d "{\"name\": \"client.resource.${PAGU_ID}\", \"type\": \"urn:token-exchange\", \"ownerManagedAccess\": false, \"scopes\": [{\"name\": \"token-exchange\"}]}" \
            2>/dev/null || echo '{}')
        TE_RESOURCE_ID=$(echo "$RESOURCE_RESP" | jq -r '._id // empty')
        echo "  OK Created token-exchange resource (${TE_RESOURCE_ID})"
    else
        TE_RESOURCE_ID="$EXISTING_RESOURCE"
        echo "  - token-exchange resource already exists"
    fi

    # Create role policy for realm-management/impersonation (DCR clients get this role)
    RM_CLIENT_ID=$(curl -sf -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients?clientId=realm-management" | jq -r '.[0].id // empty')
    RM_IMPERSONATION_ROLE_ID=$(curl -sf -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${RM_CLIENT_ID}/roles/impersonation" | jq -r '.id // empty')

    EXISTING_POLICY=$(curl -sf -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}/authz/resource-server/policy?name=dcr-impersonation-role-policy" \
        | jq -r '.[0].id // empty' 2>/dev/null)
    if [ -z "$EXISTING_POLICY" ]; then
        POLICY_RESP=$(curl -sf -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}/authz/resource-server/policy/role" \
            -d "{\"name\": \"dcr-impersonation-role-policy\", \"logic\": \"POSITIVE\", \"decisionStrategy\": \"UNANIMOUS\", \"roles\": [{\"id\": \"${RM_IMPERSONATION_ROLE_ID}\", \"required\": true}]}" \
            2>/dev/null || echo '{}')
        TE_POLICY_ID=$(echo "$POLICY_RESP" | jq -r '.id // empty')
        echo "  OK Created impersonation role policy (${TE_POLICY_ID})"
    else
        TE_POLICY_ID="$EXISTING_POLICY"
        echo "  - impersonation role policy already exists"
    fi

    # Create scope permission linking resource + scope + policy
    EXISTING_PERM=$(curl -sf -H "Authorization: Bearer ${ADMIN_TOKEN}" \
        "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}/authz/resource-server/permission?name=token-exchange-impersonation" \
        | jq -r '.[0].id // empty' 2>/dev/null)
    if [ -z "$EXISTING_PERM" ] && [ -n "$TE_SCOPE_ID" ] && [ -n "$TE_RESOURCE_ID" ] && [ -n "$TE_POLICY_ID" ]; then
        curl -sf -X POST -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            -H "Content-Type: application/json" \
            "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${PAGU_ID}/authz/resource-server/permission/scope" \
            -d "{\"name\": \"token-exchange-impersonation\", \"type\": \"scope\", \"decisionStrategy\": \"UNANIMOUS\", \"logic\": \"POSITIVE\", \"resources\": [\"${TE_RESOURCE_ID}\"], \"scopes\": [\"${TE_SCOPE_ID}\"], \"policies\": [\"${TE_POLICY_ID}\"]}" \
            >/dev/null 2>&1 || true
        echo "  OK Created token-exchange scope permission"
    else
        echo "  - token-exchange scope permission already exists (or prereqs missing)"
    fi

    echo "  OK Authorization services configured for partner-agent-ui"
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "OK Keycloak seeding complete!"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Created:"
echo "  • Realm: ${REALM}"
echo "  • Roles: engineering, software, network, kubernetes, admin"
echo "  • Groups: engineering, software, network, kubernetes, admin"
echo "  • Client: partner-agent-ui (with groups mapper)"
echo "  • Users: carlos, luis, sharon, josh"
echo ""
echo "Social login providers configured:"
[ -n "${GOOGLE_CLIENT_ID}" ]    && echo "  • Google (Gmail)" || echo "  - Google: not configured"
[ -n "${GITHUB_CLIENT_ID}" ]    && echo "  • GitHub"         || echo "  - GitHub: not configured"
[ -n "${MICROSOFT_CLIENT_ID}" ] && echo "  • Microsoft"      || echo "  - Microsoft: not configured"
echo ""
echo "DCR Initial Access Token:"
[ -f /tmp/dcr_initial_access_token.txt ] && echo "  OK Saved to /tmp/dcr_initial_access_token.txt" || echo "  ✗ Not created"
echo ""
echo "Test login (local users):"
echo "  curl -X POST ${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token \\"
echo "    -d 'client_id=partner-agent-ui' \\"
echo "    -d 'grant_type=password' \\"
echo "    -d 'username=carlos' \\"
echo "    -d 'password=carlos123'"
echo ""
