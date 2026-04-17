# API Reference

## Chat (`/adk`)

```bash
# Login to get a JWT token
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "carlos@example.com", "password": "carlos123"}' | jq -r '.token')

# Send a message (requires JWT from /auth/login)
curl -X POST http://localhost:8000/adk/chat \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "My app crashes with error 500", "user": {"email": "carlos@example.com"}}'

# View audit log (request history)
curl http://localhost:8000/adk/audit \
  -H "Authorization: Bearer $TOKEN"

# View SOC 2 audit events (authentication, authorization, data access)
curl http://localhost:8000/adk/audit-events \
  -H "Authorization: Bearer $TOKEN"

# Filter by event type or outcome
curl "http://localhost:8000/adk/audit-events?event_type=authz.deny&outcome=failure" \
  -H "Authorization: Bearer $TOKEN"
```

## OPA Policy Query

```bash
# Test OPA authorization directly
curl -X POST http://localhost:8181/v1/data/partner/authorization/decision \
  -H 'Content-Type: application/json' \
  -d '{
    "input": {
      "caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
      "agent_name": "software-support",
      "delegation": {
        "user_spiffe_id": "spiffe://partner.example.com/user/carlos",
        "agent_spiffe_id": "spiffe://partner.example.com/agent/software-support",
        "user_departments": ["engineering", "software"]
      }
    }
  }'
# Response: {"result":{"allow":true,"effective_departments":["software"],...}}
```

## A2A Agent Invocation (Internal)

```bash
# Direct agent invoke (requires X-SPIFFE-ID when ENFORCE_AGENT_AUTH=true)
curl -X POST http://localhost:8001/api/v1/agents/routing-agent/invoke \
  -H 'Content-Type: application/json' \
  -H 'X-SPIFFE-ID: spiffe://partner.example.com/service/request-manager' \
  -d '{"session_id": "s1", "user_id": "u1", "message": "Hello"}'

# Specialist invoke with delegation (triggers OPA re-check at agent-service)
curl -X POST http://localhost:8001/api/v1/agents/software-support/invoke \
  -H 'Content-Type: application/json' \
  -H 'X-SPIFFE-ID: spiffe://partner.example.com/service/request-manager' \
  -H 'X-Delegation-User: spiffe://partner.example.com/user/carlos' \
  -H 'X-Delegation-Agent: spiffe://partner.example.com/agent/software-support' \
  -d '{"session_id": "s1", "user_id": "carlos@example.com", "message": "App crash",
       "transfer_context": {"departments": ["software"]}}'
```
