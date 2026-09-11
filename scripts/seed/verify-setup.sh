#!/bin/bash
# Verify all users can login
# Called by: scripts/setup.sh
# Requires: CLIENT_SECRET environment variable

set -e

KEYCLOAK_URL="${KEYCLOAK_URL:-http://localhost:8090}"
REALM="${REALM:-partner-agent}"

echo "════════════════════════════════════════════════════════════"
echo "VERIFYING SETUP"
echo "════════════════════════════════════════════════════════════"
echo ""

# Verify CLIENT_SECRET is set
if [ -z "$CLIENT_SECRET" ]; then
    echo "✗ CLIENT_SECRET environment variable not set!" >&2
    exit 1
fi

ALL_OK=true

# Test each user login
for user_pass in "carlos:carlos123" "luis:luis123" "sharon:sharon123" "josh:josh123"; do
    username=$(echo $user_pass | cut -d: -f1)
    password=$(echo $user_pass | cut -d: -f2)

    TOKEN=$(curl -s -X POST "${KEYCLOAK_URL}/realms/${REALM}/protocol/openid-connect/token" \
        -d "client_id=partner-agent-ui" \
        -d "client_secret=$CLIENT_SECRET" \
        -d "grant_type=password" \
        -d "username=${username}" \
        -d "password=${password}" | jq -r '.access_token // empty')

    if [ -n "$TOKEN" ]; then
        GROUPS=$(echo $TOKEN | cut -d. -f2 | base64 -d 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('groups', []))" 2>/dev/null || echo "[]")
        echo "  ✓ $username@example.com - Groups: $GROUPS"
    else
        echo "  ✗ $username@example.com - LOGIN FAILED"
        ALL_OK=false
    fi
done

echo ""
echo "════════════════════════════════════════════════════════════"
if [ "$ALL_OK" = true ]; then
    echo "✓ ALL USERS VERIFIED"
else
    echo "⚠️  SOME USERS FAILED VERIFICATION"
fi
echo "════════════════════════════════════════════════════════════"

# Exit with error if any user failed
[ "$ALL_OK" = true ] || exit 1
