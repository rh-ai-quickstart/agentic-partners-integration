# Partner Agent Integration - Production Ready System

## 🚀 Quick Start - ONE COMMAND

```bash
bash scripts/setup.sh
```

**This single command gives you a FULLY WORKING SYSTEM:**
- ✅ Stops all old containers (clean slate)
- ✅ Starts all infrastructure
- ✅ Seeds users & groups from config/test-users.json
- ✅ Configures confidential authentication
- ✅ Starts all services
- ✅ Verifies everything works

**Result: Ready for production use!**

---

## 👥 Test Users

| User | Password | Groups | Access |
|------|----------|--------|--------|
| carlos@example.com | carlos123 | engineering, kubernetes, software | ✅ Multiple agents |
| luis@example.com | luis123 | engineering, network | ✅ Network agents |
| sharon@example.com | sharon123 | admin + all | ✅ ALL agents |
| josh@example.com | josh123 | (none) | ❌ No access |

---

## 🖥️ Services (after setup)

| Service | Port | URL |
|---------|------|-----|
| Web UI | 3000 | http://localhost:3000/login.html |
| API | 8000 | http://localhost:8000 |
| Keycloak | 8090 | http://localhost:8090 |

---

## 🔍 Monitor AAA Flow

```bash
bash scripts/monitor.sh
```

Shows real-time:
- User messages & responses
- Token exchanges (RFC 8693)
- OPA authorization
- Audit events

---

## 📁 Structure

```
scripts/
  ├── setup.sh     ⭐ ONE COMMAND (orchestrates everything)
  ├── monitor.sh   📊 Real-time monitoring
  └── seed/        📂 Modular seeding scripts

config/
  └── test-users.json  🎯 SINGLE SOURCE OF TRUTH

policies/
  └── *.rego       ⚖️ OPA authorization rules
```

---

## 🔒 Security

✅ Production SPIRE (real X.509-SVIDs, no mocks)  
✅ Confidential OAuth clients (not public)  
✅ OPA policy-based authorization  
✅ RFC 8693 token exchange (unique JTI per hop)  
✅ Complete audit trail  

---

## 📚 Documentation

- `README.md` - This file (quick start)
- `README-SETUP.md` - Detailed setup guide
- `FINAL-STATUS.md` - System status
- `scripts/README.md` - Scripts documentation

---

**One command. Fully automated. Production ready.**

```bash
bash scripts/setup.sh
```
