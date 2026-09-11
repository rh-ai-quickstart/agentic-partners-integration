# Scripts Directory

## 🎯 Main Scripts (Root)

### **setup.sh** ⭐
**THE ONLY SCRIPT YOU NEED TO RUN**

Idempotent setup that does everything:
- Seeds Keycloak (users, groups, client)
- Configures confidential authentication  
- Starts SPIRE auto-registration
- Restarts services
- Verifies all users

**Usage:**
```bash
bash scripts/setup.sh
```

**100% idempotent** - run as many times as you want.

---

### **monitor.sh** 📊
Real-time AAA flow monitoring

Shows:
- User messages & responses
- Token exchanges (RFC 8693)
- OPA authorization decisions
- Audit events

**Usage:**
```bash
bash scripts/monitor.sh [LOG_LEVEL]
```

Levels: `INFO` (default), `WARNING`, `ERROR`

---

## 📂 Seed Scripts (seed/)

These are automatically called by `setup.sh`:

- **seed-keycloak.sh** - Seeds users & groups from config/test-users.json
- **seed-client.sh** - Configures confidential client, returns secret
- **seed-spire.sh** - Starts SPIRE auto-registration daemon
- **spire-auto-register-daemon.sh** - Background daemon (called by seed-spire.sh)
- **start-spire-auto-register.sh** - Daemon starter (called by seed-spire.sh)

**Don't call these directly** - use `setup.sh` instead.

---

## 📦 Archive

Old/auxiliary scripts in `archive/` (28 files) - kept for reference only.

---

## 🚀 Quick Start

```bash
# ONE COMMAND TO SETUP EVERYTHING:
bash scripts/setup.sh

# MONITOR THE FLOW:
bash scripts/monitor.sh
```

That's it! Everything else is automatic.

---

**Philosophy:** Two scripts at root, modular seeds called automatically
**Last Updated:** 2026-09-09
