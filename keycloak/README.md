# Keycloak Configuration

## Files

### `realm-base.json`
**Active configuration** - Minimal realm config imported on Keycloak startup.

Contains:
- Realm settings (name, SSL, session timeouts)
- No users, no groups, no clients

Users and groups are created dynamically via `scripts/seed-keycloak.sh`.

### `realm-partner.json.example`
**Reference only** - Example of full realm export with users/groups.

This is NOT imported. It's kept as documentation showing what a full realm looks like.

For actual deployment, use:
1. `realm-base.json` (imported automatically)
2. `scripts/seed-keycloak.sh` (creates users/groups via REST API)

## Adding Users

### Development
```bash
# Option 1: Edit seed script
vim ../scripts/seed-keycloak.sh
# Add: create_user "alice" "alice@example.com" "alice123" engineering software
make seed-keycloak

# Option 2: Keycloak Admin UI
http://localhost:8090 → Users → Add User
```

### Production
- LDAP/Active Directory sync
- SAML/OIDC federation  
- Keycloak REST API automation

See `docs/production-seeding.md` and `docs/user-group-management.md` for details.

## Why Dynamic Seeding?

**Old approach (static JSON):**
- ❌ Passwords in version control
- ❌ Can't sync with enterprise auth
- ❌ Changes require file edits + restarts

**New approach (dynamic seeding):**
- ✅ No hardcoded credentials
- ✅ LDAP/SAML ready
- ✅ Changes via Admin UI
- ✅ Production-ready

## Troubleshooting

### Seed script didn't run
```bash
# Check seed container logs
docker logs partner-keycloak-seed

# Re-run manually
docker-compose up keycloak-seed
# or
make seed-keycloak
```

### Users not appearing
```bash
# Check Keycloak directly
curl http://localhost:8090/admin/realms/partner-agent/users \
  -H "Authorization: Bearer $ADMIN_TOKEN" | jq

# Or use Admin UI
http://localhost:8090 → admin/admin123 → Users
```

### Reset everything
```bash
# Delete volumes and recreate
docker-compose down -v
docker-compose up -d
# Seeding runs automatically
```
