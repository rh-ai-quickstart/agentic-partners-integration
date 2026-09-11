# Configuration Management - No Hardcoded Values

## Single Sources of Truth

All configuration comes from **exactly TWO files**:

### 1. `config/test-users.json`
**Purpose**: User accounts, groups, and permissions

```json
{
  "realm": "partner-agent",
  "groups": [...],
  "users": [...]
}
```

**Used by:**
- ✅ `scripts/seed-keycloak.sh` - Creates users in Keycloak
- ✅ `partner-fullstack-chat/login.html` - Loads users dynamically
- ✅ `partner-fullstack-chat/test-login.html` - Shows test users

**To add a user:**
1. Edit `config/test-users.json`
2. Run `bash scripts/seed-keycloak.sh`
3. UI auto-updates (no rebuild needed)

### 2. `config/environment.env`
**Purpose**: ALL environment variables

```bash
DATABASE_URL=postgresql://user:pass@partner-postgres-full:5432/partner_agent
KEYCLOAK_URL=http://partner-keycloak-full:8080
SPIRE_SERVER_URL=spire-server:8081
...
```

**Used by:**
- ✅ All service containers
- ✅ All scripts
- ✅ Makefile targets

**To change a setting:**
1. Edit `config/environment.env`
2. Reload: `set -a; source config/environment.env; set +a`
3. Restart affected containers

## What's Allowed to be Hardcoded?

### ✅ ALLOWED (These are constants):
- **Docker network service names**: `partner-postgres-full`, `partner-keycloak-full`
  - Reason: Docker DNS requires stable names
  - Location: Container startup commands, docker-compose.yaml

- **SPIFFE trust domain**: `partner.example.com`
  - Reason: Part of identity architecture
  - Location: SPIRE config files

- **Default port numbers**: 8080, 8000, etc.
  - Reason: Container internal ports (mapped externally)
  - Location: Containerfiles, configs

- **Selector patterns**: `unix:uid:1001`
  - Reason: Container security model
  - Location: SPIRE registration scripts

### ❌ NOT ALLOWED (Must use config):
- ❌ User credentials → `config/test-users.json`
- ❌ Database credentials → `config/environment.env`
- ❌ API URLs → `config/environment.env`
- ❌ Client secrets → Fetched from Keycloak API
- ❌ Join tokens → Generated dynamically
- ❌ Group names → `config/test-users.json`

## How to Use

### Load ALL environment variables:
```bash
set -a
source config/environment.env
set +a
```

### Seed EVERYTHING:
```bash
make seed
```

This runs:
1. `scripts/seed-keycloak.sh` - Seeds users from `config/test-users.json`
2. `scripts/start-spire-auto-register.sh` - Starts SPIRE auto-registration daemon

### Check for hardcoded values:
```bash
# Should return 0 or only acceptable hardcoded values
grep -r "hardcoded-value" --include="*.py" --include="*.sh" .
```

## Dynamic vs Static

| Item | Type | Source | Auto-Updated? |
|------|------|--------|---------------|
| User passwords | Dynamic | config/test-users.json | ✅ Yes (via seed script) |
| Client secret | Dynamic | Keycloak API | ✅ Yes (fetched on startup) |
| Join tokens | Dynamic | SPIRE server | ✅ Yes (generated per container) |
| Workload entries | Dynamic | Auto-register daemon | ✅ Yes (monitors agents) |
| Database creds | Static | config/environment.env | ❌ No (requires restart) |
| Service URLs | Static | Docker network | ❌ No (infrastructure) |

## Validation

To verify NO inappropriate hardcoded values:

```bash
# Check for hardcoded secrets (should be 0)
grep -r "password.*=" --include="*.py" . | grep -v "POSTGRES_PASSWORD\|KEYCLOAK.*PASSWORD" | wc -l

# Check for hardcoded tokens (should be 0)
grep -r "[0-9a-f]{8}-[0-9a-f]{4}" --include="*.sh" . | grep -v "auto-register\|example" | wc -l

# Check for hardcoded client secrets (should be 0)
grep -r "M3GKaHt\|eyJhbGci" --include="*.py" --include="*.sh" . | wc -l
```

All should return **0** (zero inappropriate hardcoded values).

## Best Practices

1. **Never commit secrets** - Use `.env` files (gitignored)
2. **Always use config files** - Not inline values
3. **Document defaults** - In `config/environment.env`
4. **Validate on startup** - Check required env vars exist
5. **Fail loudly** - Don't use fallback values for security settings

---

**Last Updated**: 2026-09-09  
**Maintained by**: Configuration is code - review like code!
