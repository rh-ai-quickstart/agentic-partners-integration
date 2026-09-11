#!/bin/bash
# Configure Keycloak confidential client and get secret
# Called by: scripts/MAIN-SETUP.sh
# Outputs: CLIENT_SECRET to stdout (last line)

set -e

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8090}"
REALM="${REALM:-partner-agent}"
CLIENT_ID="${CLIENT_ID:-partner-agent-ui}"

echo "════════════════════════════════════════════════════════════"
echo "CONFIGURING CONFIDENTIAL CLIENT"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Client ID: $CLIENT_ID"
echo ""

# Get admin token
ADMIN_TOKEN=$(curl -s -X POST "${KEYCLOAK_URL}/realms/master/protocol/openid-connect/token" \
    -d "client_id=admin-cli" \
    -d "grant_type=password" \
    -d "username=admin" \
    -d "password=admin123" 2>/dev/null | jq -r '.access_token')

if [ -z "$ADMIN_TOKEN" ] || [ "$ADMIN_TOKEN" = "null" ]; then
    echo "✗ Failed to get admin token" >&2
    exit 1
fi

# Get client UUID
CLIENT_UUID=$(curl -s "${KEYCLOAK_URL}/admin/realms/${REALM}/clients?clientId=${CLIENT_ID}" \
    -H "Authorization: Bearer $ADMIN_TOKEN" | jq -r '.[0].id')

if [ -z "$CLIENT_UUID" ] || [ "$CLIENT_UUID" = "null" ]; then
    echo "✗ Client $CLIENT_ID not found" >&2
    exit 1
fi

# Configure as confidential with token exchange enabled (idempotent)
echo "[1/3] Configuring client as CONFIDENTIAL with token exchange..."
curl -s -X PUT "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "id": "'${CLIENT_UUID}'",
        "clientId": "'${CLIENT_ID}'",
        "enabled": true,
        "publicClient": false,
        "directAccessGrantsEnabled": true,
        "standardFlowEnabled": true,
        "serviceAccountsEnabled": true,
        "protocol": "openid-connect",
        "attributes": {
            "oauth2.device.authorization.grant.enabled": "false",
            "oidc.ciba.grant.enabled": "false",
            "client.secret.creation.time": "0"
        }
    }' > /dev/null

echo "  ✓ Client configured as CONFIDENTIAL"

# Enable token exchange for this client
echo "[2/3] Enabling token exchange permissions..."

# First enable permissions management
PERM_RESPONSE=$(curl -s -X PUT "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}/management/permissions" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "enabled": true
    }')

# Configure token exchange permission policy
# Get the service account user ID for this client
SERVICE_ACCOUNT_ID=$(curl -s "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}/service-account-user" \
    -H "Authorization: Bearer $ADMIN_TOKEN" | jq -r '.id')

if [ -n "$SERVICE_ACCOUNT_ID" ] && [ "$SERVICE_ACCOUNT_ID" != "null" ]; then
    echo "  ✓ Service account found: ${SERVICE_ACCOUNT_ID:0:20}..."
fi

# Set token exchange enabled attribute
curl -s -X PUT "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H "Content-Type: application/json" \
    -d '{
        "id": "'${CLIENT_UUID}'",
        "clientId": "'${CLIENT_ID}'",
        "enabled": true,
        "publicClient": false,
        "directAccessGrantsEnabled": true,
        "standardFlowEnabled": true,
        "serviceAccountsEnabled": true,
        "authorizationServicesEnabled": false,
        "protocol": "openid-connect",
        "attributes": {
            "oauth2.device.authorization.grant.enabled": "false",
            "oidc.ciba.grant.enabled": "false",
            "oauth2.token.exchange.grant.enabled": "true",
            "client.secret.creation.time": "0"
        }
    }' > /dev/null

echo "  ✓ Token exchange enabled with proper attributes"

# Get client secret
echo ""
echo "[3/3] Fetching client secret..."
CLIENT_SECRET=$(curl -s "${KEYCLOAK_URL}/admin/realms/${REALM}/clients/${CLIENT_UUID}/client-secret" \
    -H "Authorization: Bearer $ADMIN_TOKEN" | jq -r '.value')

if [ -z "$CLIENT_SECRET" ] || [ "$CLIENT_SECRET" = "null" ]; then
    echo "✗ Failed to get client secret" >&2
    exit 1
fi

echo "  ✓ Client secret obtained: ${CLIENT_SECRET:0:20}..."

echo ""
echo "════════════════════════════════════════════════════════════"
echo "✓ CLIENT CONFIGURATION COMPLETE"
echo "════════════════════════════════════════════════════════════"
echo ""

# Output secret to stdout (captured by calling script)
echo "$CLIENT_SECRET"
