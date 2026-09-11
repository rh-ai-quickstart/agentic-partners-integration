# Configuration - Single Source of Truth

## Overview

All users, groups, and permissions are defined in **one place**: `config/test-users.json`

This file is used by:
- ✅ Keycloak seed script (`scripts/seed-keycloak.sh`)
- ✅ Web UI login page (`partner-fullstack-chat/login.html`)
- ✅ Test/diagnostic page (`partner-fullstack-chat/test-login.html`)
- ✅ (Future) Agent permission policies

## File Structure

```json
{
  "realm": "partner-agent",
  "groups": [
    {
      "name": "engineering",
      "description": "Engineering department"
    }
  ],
  "users": [
    {
      "username": "carlos",
      "email": "carlos@example.com",
      "password": "carlos123",
      "firstName": "Carlos",
      "lastName": "Camacho",
      "groups": ["engineering", "software", "kubernetes"],
      "displayName": "Carlos",
      "description": "Engineering, Software depts"
    }
  ]
}
```

## How to Add a New User

1. **Edit `config/test-users.json`**:
   ```json
   {
     "username": "alice",
     "email": "alice@example.com",
     "password": "alice123",
     "firstName": "Alice",
     "lastName": "Developer",
     "groups": ["software"],
     "displayName": "Alice",
     "description": "Software department"
   }
   ```

2. **Run the seed script**:
   ```bash
   bash scripts/seed-keycloak.sh
   ```

3. **Deploy config to web UI**:
   ```bash
   docker cp config/test-users.json partner-pf-chat-ui:/opt/app-root/src/config/test-users.json
   ```

4. **Refresh login page** - new user appears automatically!

## How to Add a New Group

1. **Add to `groups` array in `test-users.json`**:
   ```json
   {
     "name": "devops",
     "description": "DevOps team"
   }
   ```

2. **Run seed script** (same as above)

3. **Assign to users** by adding `"devops"` to their `groups` array

## Current Users

| User | Email | Password | Groups |
|------|-------|----------|--------|
| Carlos | carlos@example.com | carlos123 | engineering, software, kubernetes |
| Luis | luis@example.com | luis123 | engineering, network |
| Sharon | sharon@example.com | sharon123 | admin (all groups) |
| Josh | josh@example.com | josh123 | (none) |

## Benefits

✅ **No Duplication** - Define users once, use everywhere
✅ **Easy to Update** - Change JSON → re-seed → done
✅ **Version Controlled** - Users config is in git
✅ **Type Safe** - JSON schema can be validated
✅ **Self-Documenting** - UI reads directly from config

## Testing

Test all users:
```bash
# From config
jq -r '.users[] | "\(.email) / \(.password)"' config/test-users.json

# Test login
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"carlos@example.com","password":"carlos123"}'
```

Web UI:
- Main: http://localhost:3000/login.html
- Test: http://localhost:3000/test-login.html

Both pages load users from `config/test-users.json` dynamically!

## Future Extensions

- [ ] Add agent capabilities to user config
- [ ] Add OPA policies based on groups
- [ ] Add client secrets to config
- [ ] Add permission matrix visualization

---

**Last Updated**: 2026-09-09
**Maintained By**: System automatically reads this config - no manual sync needed!
