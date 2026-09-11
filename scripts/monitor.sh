#!/bin/bash
# Real-Time AAA Flow Monitor — Full delegation + token chain + creator/verifier view
#
# Usage:
#   bash scripts/monitor.sh          # live stream (logs + DB)
#   bash scripts/monitor.sh --db     # DB-only audit tail

set -e
DB_ONLY=false
[[ "${1}" == "--db" ]] && DB_ONLY=true

# ── colours ──────────────────────────────────────────────────────────────────
R='\033[0;31m'   # red
G='\033[0;32m'   # green
Y='\033[1;33m'   # yellow
B='\033[0;34m'   # blue
C='\033[0;36m'   # cyan
M='\033[0;35m'   # magenta
W='\033[1;37m'   # bold white
D='\033[2m'      # dim
U='\033[4m'      # underline
N='\033[0m'      # reset

# ── helpers ───────────────────────────────────────────────────────────────────
short_id() { echo "$1" | sed 's|spiffe://[^/]*/||'; }
trunc()    { local s="$1" n="${2:-80}"; [ ${#s} -gt $n ] && echo "${s:0:$n}…" || echo "$s"; }
py()       { python3 -c "import sys,json; d=json.load(sys.stdin); $1" 2>/dev/null; }

# ── static flow diagram (printed once at startup) ─────────────────────────────
print_diagram() {
clear
cat << 'DIAGRAM'

═══════════════════════════════════════════════════════════════════════════════
  🔐  AAA FLOW MONITOR  —  creator · verifier · delegation chain · SPIFFE IDs
═══════════════════════════════════════════════════════════════════════════════

  ┌─ SERVICES EXPLAINED (plain English) ────────────────────────────────────┐
  │                                                                          │
  │  🏛  KEYCLOAK  —  the "passport office"                                  │
  │      Every user and service needs a passport (JWT token) to talk to     │
  │      anyone else. Keycloak issues and signs those passports. It also     │
  │      does "On-Behalf-Of" swaps: it takes your passport and prints a      │
  │      new one that says "request-manager is acting for carlos" — so the   │
  │      specialist agent knows exactly who the original caller was.         │
  │                                                                          │
  │  🪪  SPIRE / SPIFFE  —  the "employee badge scanner"                     │
  │      Containers don't have usernames or passwords. SPIRE gives each      │
  │      running service a cryptographic badge (called an SVID) based on     │
  │      which Docker container it is. The badge says "I am the request-     │
  │      manager service, proved by my container label." No secrets stored   │
  │      anywhere — the badge rotates automatically every few hours.         │
  │      request-manager shows this badge to Keycloak to prove it's a        │
  │      trusted service before Keycloak will issue a new scoped token.      │
  │                                                                          │
  │  ⚖️  OPA  —  the "bouncer with a rulebook"                               │
  │      Before any agent is called, OPA checks a policy: does this user     │
  │      have permission to talk to that agent? The policy is simple:        │
  │      user_departments ∩ agent_departments must not be empty. E.g.        │
  │      carlos has [engineering, software, kubernetes]. The kubernetes-      │
  │      support agent needs [kubernetes]. Intersection = [kubernetes] ✓.    │
  │      If the intersection is empty, OPA says NO and no token is minted.   │
  │      OPA runs twice per request (defense-in-depth): once in the          │
  │      orchestrator (request-manager) and once inside the agent-service.   │
  │                                                                          │
  │  🔄  TOKEN EXCHANGE (RFC 8693)  —  the "scoped day-pass"                │
  │      Carlos's login token is like a master key. You don't hand a master  │
  │      key to a specialist — you print a one-day pass that only opens      │
  │      the kubernetes-support room. That's what token exchange does:       │
  │      it takes the login token and prints a new JWT whose "aud" (audience)│
  │      field is locked to one specific agent. Any other agent that receives │
  │      it will reject it immediately (audience mismatch). The new token     │
  │      also carries an "act" claim proving who did the swap.               │
  │                                                                          │
  │  🤖  AGENT SERVICE  —  the "specialist desk"                             │
  │      Runs the actual AI agents. Each agent checks the incoming token      │
  │      (is the audience correct?), calls OPA again (is this caller         │
  │      allowed?), then queries the RAG knowledge base and sends the        │
  │      question to the LLM with the retrieved context.                     │
  │                                                                          │
  │  📦  RAG API  —  the "filing cabinet"                                    │
  │      Stores support tickets as vector embeddings. When an agent gets a   │
  │      question it searches for semantically similar past tickets and       │
  │      injects them into the LLM prompt — so the AI answers based on       │
  │      real historical cases, not just general knowledge.                  │
  │                                                                          │
  └──────────────────────────────────────────────────────────────────────────┘

  FULL REQUEST FLOW (one chat message = all steps below, in order)
  ─────────────────────────────────────────────────────────────────

  [Browser / curl]
       │  POST /adk/chat  {message, user.email}
       │  Authorization: Bearer <Token₀>          ← Keycloak login token
       ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  REQUEST MANAGER  (spiffe://…/request-manager)                       │
  │                                                                      │
  │  Step 1 ── IDENTITY BOOTSTRAP (SPIRE)                                │
  │    SPIRE agent (unix socket) issues JWT-SVID to this process         │
  │    CREATOR  : SPIRE Server  (workload attestation via Docker label)  │
  │    VERIFIER : Keycloak token-exchange endpoint (next step)           │
  │    RESULT   : spiffe://partner.example.com/request-manager           │
  │                                                                      │
  │  Step 2 ── TOKEN EXCHANGE  (RFC 8693 On-Behalf-Of, call Keycloak)   │
  │    Presents Token₀ as subject_token                                  │
  │    CREATOR  : Keycloak  POST /realms/…/protocol/openid-connect/token │
  │    VERIFIER : agent-service (validates against Keycloak JWKS)        │
  │    RESULT   : Token₁  {sub: carlos, aud: spiffe://…/routing-agent,  │
  │                         act: {sub: request-manager}}                 │
  │                                                                      │
  │  Step 3 ── OPA AUTHORIZATION  Layer 1  (call OPA)                   │
  │    CALLER   : request-manager                                        │
  │    EVALUATED: OPA  POST /v1/data/partner/authorization/decision      │
  │    CHECK    : user_departments ∩ agent_capabilities ≠ ∅             │
  │    RESULT   : effective_departments (narrowed scope for specialist)  │
  │                                                                      │
  │  Step 4 ── A2A HTTP CALL → routing-agent                            │
  │    Headers  : Authorization: Bearer Token₁                           │
  │               X-SPIFFE-ID: spiffe://…/request-manager               │
  │               X-Delegation-User: spiffe://…/user/carlos@…           │
  └──────────────────────────────────────────────────────────────────────┘
       │
       ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  AGENT SERVICE — routing-agent                                       │
  │                                                                      │
  │  Step 5 ── TOKEN VERIFICATION                                        │
  │    VERIFIER : agent-service validates Token₁ signature              │
  │               via Keycloak JWKS endpoint (caches public keys)        │
  │    CHECKS   : aud == spiffe://…/routing-agent  ← must match exactly │
  │               sig valid, not expired                                 │
  │                                                                      │
  │  Step 6 ── OPA AUTHORIZATION  Layer 2  (defense-in-depth)           │
  │    CALLER   : agent-service                                          │
  │    EVALUATED: OPA  POST /v1/data/partner/authorization/decision      │
  │    INPUT    : caller SPIFFE ID + delegation user + departments       │
  │    RESULT   : allow/deny (independent second gate)                   │
  │                                                                      │
  │  Step 7 ── LLM ROUTING DECISION                                      │
  │    Returns  : ROUTE:kubernetes-support  (or conversational reply)    │
  └──────────────────────────────────────────────────────────────────────┘
       │  routing-agent returns ROUTE:kubernetes-support
       ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  REQUEST MANAGER  (back to orchestrator)                             │
  │                                                                      │
  │  Step 8 ── SCOPE REDUCTION                                           │
  │    OPA result narrows departments:  user_all ⊇ effective_subset     │
  │    Specialist only sees departments it's authorized for              │
  │                                                                      │
  │  Step 9 ── TOKEN EXCHANGE #2  (new scoped token for specialist)     │
  │    CREATOR  : Keycloak                                               │
  │    RESULT   : Token₂  {sub: carlos, aud: spiffe://…/k8s-support,   │
  │                         act: {sub: request-manager}}                 │
  │                                                                      │
  │  Step 10 ── OPA AUTHORIZATION Layer 1 again (for specialist)        │
  │  Step 11 ── A2A HTTP CALL → kubernetes-support  (Token₂)            │
  └──────────────────────────────────────────────────────────────────────┘
       │
       ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  AGENT SERVICE — kubernetes-support                                  │
  │                                                                      │
  │  Steps 12-13 ── Token verification + OPA Layer 2 (same as above)   │
  │  Step 14 ── RAG query (pgvector semantic search)                     │
  │  Step 15 ── LLM call with RAG context + conversation history        │
  │  Step 16 ── Response → request-manager → browser                    │
  │  Step 17 ── Audit record written to PostgreSQL request_logs          │
  └──────────────────────────────────────────────────────────────────────┘

  Live events below.  Legend:
    🔑 Login   🪪 SPIRE SVID   🔄 Token exchange   ⚖ OPA check
    🗺 Route   🤖 Agent call   💡 Response          📋 Request log
───────────────────────────────────────────────────────────────────────────────

DIAGRAM
}

# ── DB audit tail ─────────────────────────────────────────────────────────────
audit_tail() {
    local last_id=0
    last_id=$(docker exec partner-postgres-full psql -U user -d partner_agent -t -A \
        -c "SELECT COALESCE(MAX(id),0) FROM audit_events" 2>/dev/null | tr -d '[:space:]')

    while true; do
        sleep 2
        # Process substitution keeps last_id in the parent shell scope so it
        # advances each cycle.  SOH (chr 1) as delimiter avoids splitting on
        # pipe characters that appear inside JSON metadata values.
        while IFS=$'\x01' read -r id ts etype actor action resource outcome meta; do
            [ -z "$id" ] && continue
            last_id=$id

            case "$etype" in

            # ── LOGIN ──────────────────────────────────────────────────────
            auth.login.success)
                depts=$(echo "$meta" | grep -oP '"departments":\s*\K\[[^\]]*\]' || echo "[]")
                role=$(echo  "$meta" | grep -oP '"role":\s*"\K[^"]*')
                printf "\n${G}┌─ 🔑 LOGIN  [${ts}]${N}\n"
                printf "${G}│${N}  ${D}CREATOR  : Keycloak (password grant → issues JWT)${N}\n"
                printf "${G}│${N}  ${D}VERIFIER : request-manager (decodes + stores token)${N}\n"
                printf "${G}│${N}  📖 ${D}WHY: The user typed their password into the UI. Keycloak checked it${N}\n"
                printf "${G}│${N}  ${D}     against its user database, then printed a signed JWT (Token₀) that${N}\n"
                printf "${G}│${N}  ${D}     says \"this is carlos and he belongs to these groups\". Token₀ is${N}\n"
                printf "${G}│${N}  ${D}     kept by request-manager for the duration of this session.${N}\n"
                printf "${G}│${N}  ────────────────────────────────────────────────\n"
                printf "${G}│${N}  user     : ${W}${actor}${N}\n"
                printf "${G}│${N}  role     : ${role}\n"
                printf "${G}│${N}  groups   : ${C}${depts}${N}\n"
                printf "${G}│${N}  token    : ${D}Token₀  aud=partner-agent-ui  (login token)${N}\n"
                printf "${G}└─ ✓ Token₀ stored in CredentialService for this request${N}\n"
                ;;

            auth.login.failure)
                printf "\n${R}┌─ ✗ LOGIN FAILED  [${ts}]${N}\n"
                printf "${R}│${N}  ${D}VERIFIER : Keycloak rejected credentials${N}\n"
                printf "${R}│${N}  user   : ${actor}\n"
                printf "${R}└─ reason: $(echo "$meta" | grep -oP '"reason":\s*"\K[^"]*')${N}\n"
                ;;

            # ── USER MESSAGE ───────────────────────────────────────────────
            data.chat.request)
                msg_len=$(echo "$meta" | grep -oP '"message_length":\s*\K[0-9]+' || echo "?")
                sid=$(echo "$meta"     | grep -oP '"session_id":\s*"\K[^"]*' | cut -c1-8 2>/dev/null || echo "?")
                printf "\n${C}┌─ 💬 USER MESSAGE  [${ts}]${N}\n"
                printf "${C}│${N}  ${D}RECEIVER : request-manager /adk/chat endpoint${N}\n"
                printf "${C}│${N}  📖 ${D}WHY: The browser sent the user's typed message to the orchestrator.${N}\n"
                printf "${C}│${N}  ${D}     Before calling any AI agent, request-manager must (1) prove its${N}\n"
                printf "${C}│${N}  ${D}     own identity via SPIRE, (2) swap Token₀ for a scoped token, and${N}\n"
                printf "${C}│${N}  ${D}     (3) get OPA's permission. Only then does the AI get the message.${N}\n"
                printf "${C}│${N}  ────────────────────────────────────────────────\n"
                printf "${C}│${N}  from     : ${W}${actor}${N}\n"
                printf "${C}│${N}  session  : ${D}${sid}…${N}\n"
                printf "${C}│${N}  length   : ${msg_len} chars\n"
                printf "${C}└─ → SPIRE SVID fetch next, then token exchange${N}\n"
                ;;

            # ── TOKEN EXCHANGE ─────────────────────────────────────────────
            token.exchange)
                orig=$(echo      "$meta" | grep -oP '"original_aud":\s*"\K[^"]*'      || echo "Token₀")
                newaud=$(echo    "$meta" | grep -oP '"new_aud":\s*"\K[^"]*'            || echo "?")
                act_svc=$(echo   "$meta" | grep -oP '"actor_service":\s*"\K[^"]*'      || echo "request-manager")
                exp=$(echo       "$meta" | grep -oP '"expires_in":\s*\K[0-9]+'         || echo "?")
                auth_m=$(echo    "$meta" | grep -oP '"auth_method":\s*"\K[^"]*'        || echo "legacy")
                exc_cid=$(echo   "$meta" | grep -oP '"exchange_client_id":\s*"\K[^"]*' || echo "")
                target_short=$(short_id "$newaud")

                # Determine token label (Token₁ for routing-agent, Token₂ for specialist)
                if echo "$newaud" | grep -q "routing-agent"; then
                    tok_label="Token₁"
                else
                    tok_label="Token₂"
                fi

                # Auth method label and icon
                if [ "$auth_m" = "dcr-actor" ]; then
                    auth_label="${G}DCR actor-token (RFC 8693 §4.1)${N}"
                    cid_display="partner-agent-ui + DCR actor"
                elif [ "$auth_m" = "dcr" ]; then
                    auth_label="${G}DCR (per-service UUID)${N}"
                    cid_display="${exc_cid:0:8}…"
                elif [ "$auth_m" = "legacy-fallback" ]; then
                    auth_label="${Y}legacy-fallback (partner-agent-ui)${N}"
                    cid_display="partner-agent-ui"
                else
                    auth_label="${D}legacy (partner-agent-ui)${N}"
                    cid_display="partner-agent-ui"
                fi

                printf "\n${M}┌─ 🔄 TOKEN EXCHANGE  [${ts}]  RFC 8693 On-Behalf-Of${N}\n"
                printf "${M}│${N}  ${D}CREATOR  : Keycloak  POST /realms/…/protocol/openid-connect/token${N}\n"
                printf "${M}│${N}  ${D}CALLER   : ${act_svc} (presents SVID + Token₀ as subject_token)${N}\n"
                printf "${M}│${N}  ${D}VERIFIER : agent-service (validates via Keycloak JWKS on receipt)${N}\n"
                printf "${M}│${N}  🔑 AUTH    : ${auth_label}  client_id=${D}${cid_display}${N}\n"
                printf "${M}│${N}  📖 ${D}WHY: request-manager cannot hand carlos's login token directly to an${N}\n"
                printf "${M}│${N}  ${D}     agent — the agent would have no way to know it was meant for it.${N}\n"
                printf "${M}│${N}  ${D}     Instead request-manager calls Keycloak's token-swap endpoint,${N}\n"
                printf "${M}│${N}  ${D}     presenting its SPIRE badge + Token₀, and says \"give me a new token${N}\n"
                printf "${M}│${N}  ${D}     that is locked to agent/$(short_id "$newaud")\". Keycloak prints${N}\n"
                printf "${M}│${N}  ${D}     ${tok_label} with aud=$(short_id "$newaud"). Any other agent${N}\n"
                printf "${M}│${N}  ${D}     that receives ${tok_label} will reject it: wrong audience.${N}\n"
                printf "${M}│${N}  ────────────────────────────────────────────────\n"
                printf "${M}│${N}\n"
                printf "${M}│  ${U}Delegation chain:${N}\n"
                printf "${M}│${N}    ${W}${actor}${N}  (orchestrator)\n"
                printf "${M}│${N}      └─ acting on behalf of → ${W}$(short_id "$orig")${N}  (original caller)\n"
                printf "${M}│${N}           └─ minting token for → ${C}${target_short}${N}  (target agent)\n"
                printf "${M}│${N}\n"
                printf "${M}│  ${U}New JWT (${tok_label}) structure:${N}\n"
                printf "${M}│${N}    sub  : original user subject               ${D}← identity preserved${N}\n"
                printf "${M}│${N}    aud  : ${C}${target_short}${N}  ${D}← ONLY this agent can accept it${N}\n"
                printf "${M}│${N}    act  : {sub: \"${act_svc}\"}               ${D}← delegation proof${N}\n"
                printf "${M}│${N}    ttl  : ${exp}s\n"
                printf "${M}│${N}\n"
                printf "${M}│  old token: ${D}$(trunc "$orig" 55)${N}\n"
                printf "${M}│  new token: ${C}$(trunc "$newaud" 55)${N}\n"
                printf "${M}└─ ✓ ${tok_label} issued — scoped to ${target_short}  🔑 via ${auth_m}${N}\n"
                ;;

            # ── OPA AUTHORIZATION ──────────────────────────────────────────
            authz.allow)
                caller=$(echo  "$meta" | grep -oP '"caller":\s*"\K[^"]*'              || echo "")
                eff=$(echo     "$meta" | grep -oP '"effective_departments":\s*\K\[[^\]]*\]' || echo "[]")
                layer=$(echo   "$meta" | grep -oP '"layer":\s*"\K[^"]*'              || echo "")
                depts=$(echo   "$meta" | grep -oP '"departments":\s*\K\[[^\]]*\]'    || echo "")
                actor_s=$(short_id "$actor")
                caller_s=$(short_id "$caller")
                res_s=$(short_id "$resource")

                # Determine which layer this is and who called OPA
                if [ "$layer" = "defense-in-depth" ]; then
                    opa_caller="agent-service"
                    layer_note="  ${D}[Layer 2 — defense-in-depth inside agent-service]${N}"
                else
                    opa_caller="request-manager"
                    layer_note="  ${D}[Layer 1 — orchestrator gate]${N}"
                fi

                printf "\n${B}┌─ ⚖  OPA ALLOW  [${ts}]${layer_note}\n"
                printf "${B}│${N}  ${D}CALLER   : ${opa_caller}  POST /v1/data/partner/authorization/decision${N}\n"
                printf "${B}│${N}  ${D}EVALUATED: OPA engine (Rego policy from agent YAML capabilities)${N}\n"
                if [ "$layer" = "defense-in-depth" ]; then
                printf "${B}│${N}  📖 ${D}WHY: agent-service does NOT trust the orchestrator blindly. Even${N}\n"
                printf "${B}│${N}  ${D}     though request-manager already checked OPA, agent-service runs its${N}\n"
                printf "${B}│${N}  ${D}     own independent check. This is defense-in-depth: if the orchestrator${N}\n"
                printf "${B}│${N}  ${D}     were ever compromised, a rogue call would still be blocked here.${N}\n"
                printf "${B}│${N}  ${D}     OPA reads the caller's SPIFFE ID from the X-SPIFFE-ID header and${N}\n"
                printf "${B}│${N}  ${D}     the delegation user from X-Delegation-User, then re-evaluates the${N}\n"
                printf "${B}│${N}  ${D}     same department-intersection policy. Both gates must say ALLOW.${N}\n"
                else
                printf "${B}│${N}  📖 ${D}WHY: OPA is the bouncer. Before spending time on a token exchange or${N}\n"
                printf "${B}│${N}  ${D}     an LLM call, request-manager asks OPA: \"is ${actor_s}${N}\n"
                printf "${B}│${N}  ${D}     allowed to reach ${res_s}?\" OPA computes the intersection of the${N}\n"
                printf "${B}│${N}  ${D}     user's department list and the agent's required departments. If the${N}\n"
                printf "${B}│${N}  ${D}     intersection is empty the request is blocked here — no token is${N}\n"
                printf "${B}│${N}  ${D}     minted, no agent is called, and the user gets an access-denied msg.${N}\n"
                fi
                printf "${B}│${N}  ────────────────────────────────────────────────\n"
                printf "${B}│${N}\n"
                printf "${B}│  ${U}Permission check:${N}\n"
                printf "${B}│${N}    who     : ${W}${actor_s}${N}\n"
                [ -n "$caller_s" ] && \
                printf "${B}│${N}    via     : ${caller_s}  ${D}(X-SPIFFE-ID on inbound call)${N}\n"
                printf "${B}│${N}    wants   : invoke ${C}${res_s}${N}\n"
                printf "${B}│${N}    action  : ${action}\n"
                printf "${B}│${N}\n"
                printf "${B}│  ${U}Department intersection  (user_depts ∩ agent_capabilities):${N}\n"
                [ -n "$depts" ] && \
                printf "${B}│${N}    user has    : ${depts}\n"
                printf "${B}│${N}    effective   : ${G}${eff}${N}  ${D}← narrowed scope passed to specialist${N}\n"
                printf "${B}└─ ${G}✓ ALLOW${N}  — delegation authorized\n"
                ;;

            authz.deny)
                layer=$(echo "$meta" | grep -oP '"layer":\s*"\K[^"]*' || echo "")
                reason=$(echo "$meta" | grep -oP '"opa_reason":\s*"\K[^"]*' || echo "?")
                [ "$layer" = "defense-in-depth" ] && opa_caller="agent-service" || opa_caller="request-manager"
                printf "\n${R}┌─ ✗ OPA DENY  [${ts}]${N}\n"
                printf "${R}│${N}  ${D}CALLER   : ${opa_caller}${N}\n"
                printf "${R}│${N}  ${D}EVALUATED: OPA — departments ∩ capabilities = ∅${N}\n"
                printf "${R}│${N}  📖 ${D}WHY: The user asked about something outside their department access.${N}\n"
                printf "${R}│${N}  ${D}     OPA computed the intersection of the user's groups and the agent's${N}\n"
                printf "${R}│${N}  ${D}     required groups and got an empty set. No token was minted and the${N}\n"
                printf "${R}│${N}  ${D}     specialist was never called — the user sees an access-denied message.${N}\n"
                printf "${R}│${N}  actor    : $(short_id "$actor")\n"
                printf "${R}│${N}  resource : ${resource}\n"
                printf "${R}│${N}  reason   : ${reason}\n"
                printf "${R}└─ ✗ request blocked — no specialist call made${N}\n"
                ;;

            authz.routing_direct)
                printf "\n${D}┌─ ↩  HANDLED BY ROUTING-AGENT  [${ts}]${N}\n"
                printf "${D}│${N}  routing-agent answered directly (greetings / out-of-scope)\n"
                printf "${D}│${N}  No specialist token exchange or OPA check needed\n"
                printf "${D}└─ response returned to user${N}\n"
                ;;

            *)
                printf "${Y}  📝  ${etype}${N}  ${D}actor=${actor} resource=${resource} outcome=${outcome}${N}\n"
                ;;
            esac

        done < <(docker exec partner-postgres-full psql -U user -d partner_agent -t -A \
            -F$'\x01' \
            -c "SELECT id,
                       to_char(created_at AT TIME ZONE 'UTC','HH24:MI:SS'),
                       event_type, actor, action, resource, outcome,
                       metadata::text
                FROM audit_events
                WHERE id > ${last_id}
                  AND created_at >= '${MONITOR_START}'
                ORDER BY id ASC" 2>/dev/null)
    done
}

# ── log stream parser ─────────────────────────────────────────────────────────
parse_logs() {
    while IFS= read -r line; do
        # Plain-text lines (not JSON) — catch SPIRE SVID fetch
        if ! echo "$line" | grep -q '^{'; then
            if echo "$line" | grep -q "DCR registration successful"; then
                spiffe=$(echo "$line" | grep -oP "spiffe_id=\K[^ ']+" || echo "unknown")
                kc_client=$(echo "$line" | grep -oP "keycloak_client=\K[^ ]+" || echo "unknown")
                ts=$(date +%H:%M:%S)
                printf "\n${G}╔══ 🔑 DCR SELF-REGISTRATION  [${ts}]${N}\n"
                printf "${G}║${N}  ${D}CREATOR  : Keycloak DCR endpoint (RFC 7591)${N}\n"
                printf "${G}║${N}  ${D}CALLER   : ${svc} (using Initial Access Token from seed-keycloak.sh)${N}\n"
                printf "${G}║${N}  ${D}RESULT   : new Keycloak client minted on the fly, no pre-seeding${N}\n"
                printf "${G}║${N}  📖 ${D}WHY: Instead of pre-registering every agent in Keycloak via seed${N}\n"
                printf "${G}║${N}  ${D}     scripts, each service registers itself on startup.  This is${N}\n"
                printf "${G}║${N}  ${D}     Dynamic Client Registration (RFC 7591): the agent POSTs to${N}\n"
                printf "${G}║${N}  ${D}     Keycloak's /clients-registrations endpoint with an Initial${N}\n"
                printf "${G}║${N}  ${D}     Access Token as proof of authorisation.  Keycloak generates a${N}\n"
                printf "${G}║${N}  ${D}     UUID client_id and Registration Access Token (RAT) for future${N}\n"
                printf "${G}║${N}  ${D}     management.  No secrets are stored in the codebase.${N}\n"
                printf "${G}║${N}  ────────────────────────────────────────────────\n"
                printf "${G}║${N}  service     : ${W}${svc}${N}\n"
                printf "${G}║${N}  SPIFFE ID   : ${W}${spiffe}${N}\n"
                printf "${G}║${N}  Keycloak ID : ${D}${kc_client}${N}  ${D}(UUID generated by Keycloak)${N}\n"
                printf "${G}╚══ agent is now a first-class Keycloak client${N}\n"
                continue
            fi
            if echo "$line" | grep -q "Fetched SVID from SPIRE:"; then
                svid=$(echo "$line" | sed 's/.*Fetched SVID from SPIRE: //')
                ts=$(date +%H:%M:%S)
                printf "\n${Y}┌─ 🪪 SPIRE SVID ISSUED  [${ts}]${N}\n"
                printf "${Y}│${N}  ${D}CREATOR  : SPIRE Server (workload attestation via Docker label)${N}\n"
                printf "${Y}│${N}  ${D}ISSUED TO: request-manager process (via Unix socket /run/spire/sockets/agent.sock)${N}\n"
                printf "${Y}│${N}  ${D}USED FOR : Keycloak token exchange (proves service identity)${N}\n"
                printf "${Y}│${N}  📖 ${D}WHY: Keycloak will only do a token exchange for services it trusts.${N}\n"
                printf "${Y}│${N}  ${D}     But how does Keycloak know who is calling? request-manager has no${N}\n"
                printf "${Y}│${N}  ${D}     password. Instead SPIRE looks at its Docker container label, confirms${N}\n"
                printf "${Y}│${N}  ${D}     it is the \"request-manager\" workload, and issues a short-lived${N}\n"
                printf "${Y}│${N}  ${D}     cryptographic badge (SVID). This badge is then presented to Keycloak${N}\n"
                printf "${Y}│${N}  ${D}     as proof: \"I am the request-manager service, not some random caller.\"${N}\n"
                printf "${Y}│${N}  ────────────────────────────────────────────────\n"
                printf "${Y}│${N}  SPIFFE ID : ${W}${svid}${N}\n"
                printf "${Y}│${N}  type      : JWT-SVID  (short-lived, auto-rotated)\n"
                printf "${Y}│${N}  trust     : spiffe://partner.example.com\n"
                printf "${Y}└─ → SVID presented to Keycloak for token exchange${N}\n"
            fi
            continue
        fi

        event=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('event',''))" 2>/dev/null)
        ts=$(echo    "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('timestamp','')[11:19])" 2>/dev/null)
        svc=$(echo   "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('service',''))" 2>/dev/null)

        case "$event" in

        # ── DCR SELF-REGISTRATION (JSON structured log) ────────────────────
        "DCR self-registration complete")
            spiffe=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('extra',{}).get('spiffe_id','unknown'))" 2>/dev/null)
            printf "\n${G}╔══ 🔑 DCR REGISTERED + READY  [${ts}]${N}\n"
            printf "${G}║${N}  ${D}CALLER   : ${svc}${N}\n"
            printf "${G}║${N}  SPIFFE ID: ${W}${spiffe}${N}\n"
            printf "${G}║${N}  ${D}• Self-registered as Keycloak client (no pre-seeding)${N}\n"
            printf "${G}║${N}  ${D}• Granted realm-management/impersonation for token exchange${N}\n"
            printf "${G}║${N}  ${D}• DCR credentials will be used as actor_token in RFC 8693 exchanges${N}\n"
            printf "${G}╚══ DCR service identity fully operational${N}\n"
            ;;

        # ── ACTUAL USER PROMPT ─────────────────────────────────────────────
        "Routing to routing-agent for conversation handling")
            user=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('user',''))" 2>/dev/null)
            msg=$(echo  "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('message',''))" 2>/dev/null)
            [ -z "$msg" ] && continue
            printf "\n${C}┌─ 💬 PROMPT  [${ts}]${N}\n"
            printf "${C}│${N}  ${D}RECEIVED BY: request-manager /adk/chat${N}\n"
            printf "${C}│${N}  📖 ${D}WHY: This is the raw text the user typed. The message will NOT be sent${N}\n"
            printf "${C}│${N}  ${D}     to any AI yet. First: (1) fetch SPIRE SVID, (2) exchange Token₀ for${N}\n"
            printf "${C}│${N}  ${D}     a scoped token, (3) OPA permission check. Only after all three pass${N}\n"
            printf "${C}│${N}  ${D}     does the routing-agent LLM receive the message.${N}\n"
            printf "${C}│${N}  ────────────────────────────────────────────────\n"
            printf "${C}│${N}  user    : ${W}${user}${N}\n"
            printf "${C}│${N}  message : \"${W}$(trunc "$msg" 110)${N}\"\n"
            printf "${C}└─ → fetching SPIRE SVID then exchanging tokens${N}\n"
            ;;

        # ── ROUTING DECISION ───────────────────────────────────────────────
        "Routing decision received (OPA authorized)")
            from=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('from_agent',''))" 2>/dev/null)
            to=$(echo   "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('to_agent',''))" 2>/dev/null)
            eff=$(echo  "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('effective_departments','[]'))" 2>/dev/null)
            printf "\n${Y}┌─ 🗺  ROUTING DECISION  [${ts}]${N}\n"
            printf "${Y}│${N}  ${D}DECIDED BY : routing-agent LLM (ROUTE: marker in response)${N}\n"
            printf "${Y}│${N}  ${D}AUTHORIZED : request-manager confirmed via OPA (Layer 1)${N}\n"
            printf "${Y}│${N}  📖 ${D}WHY: The routing-agent LLM read the user's message and returned a${N}\n"
            printf "${Y}│${N}  ${D}     special marker \"ROUTE:${to}\" meaning \"send this to that specialist\".${N}\n"
            printf "${Y}│${N}  ${D}     Before actually doing so, request-manager asked OPA \"is this user${N}\n"
            printf "${Y}│${N}  ${D}     allowed to reach ${to}?\" OPA said yes and also narrowed${N}\n"
            printf "${Y}│${N}  ${D}     the departments to ${eff} — so the specialist only sees${N}\n"
            printf "${Y}│${N}  ${D}     what it needs, not the user's full group list.${N}\n"
            printf "${Y}│${N}  ────────────────────────────────────────────────\n"
            printf "${Y}│${N}  from             : ${from}\n"
            printf "${Y}│${N}  to               : ${W}${to}${N}\n"
            printf "${Y}│${N}  effective depts  : ${G}${eff}${N}  ${D}← scope reduced by OPA intersection${N}\n"
            printf "${Y}└─ → new token exchange for ${to} next${N}\n"
            ;;

        # ── AGENT INVOCATION ───────────────────────────────────────────────
        "Invoking agent")
            url=$(echo  "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('url',''))" 2>/dev/null)
            [ -z "$url" ] && continue   # skip pre-exchange line
            agent=$(echo  "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('agent_name',''))" 2>/dev/null)
            hop=$(echo    "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('hop','?'))" 2>/dev/null)
            prev=$(echo   "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('previous_agent') or 'none')" 2>/dev/null)
            sess=$(echo   "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('session_id','')[:8])" 2>/dev/null)
            target=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('target_agent',''))" 2>/dev/null)
            tid=$(echo    "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token_id',''))" 2>/dev/null)
            deleg=$(echo  "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('is_delegated_token',False))" 2>/dev/null)
            [ "$hop" = "1" ] && tok_label="Token₁" || tok_label="Token₂"

            printf "\n${M}┌─ 🤖 AGENT CALL  [${ts}]  hop ${hop}  (${svc} → ${agent})${N}\n"
            printf "${M}│${N}  ${D}CALLER   : request-manager  (spiffe://partner.example.com/request-manager)${N}\n"
            printf "${M}│${N}  ${D}VERIFIER : agent-service — checks token sig via Keycloak JWKS${N}\n"
            printf "${M}│${N}  ${D}           then OPA Layer 2 (defense-in-depth)${N}\n"
            printf "${M}│${N}  📖 ${D}WHY: request-manager makes an HTTP POST to the agent-service. It sends${N}\n"
            printf "${M}│${N}  ${D}     three key headers: the scoped JWT so agent-service can verify who is${N}\n"
            printf "${M}│${N}  ${D}     allowed in; its SPIRE SVID so agent-service knows the caller is the${N}\n"
            printf "${M}│${N}  ${D}     legitimate orchestrator; and the delegation header so agent-service${N}\n"
            printf "${M}│${N}  ${D}     knows it is acting on behalf of a real user (not calling for itself).${N}\n"
            printf "${M}│${N}  ────────────────────────────────────────────────\n"
            printf "${M}│${N}  agent      : ${W}${agent}${N}\n"
            printf "${M}│${N}  endpoint   : ${D}${url}${N}\n"
            printf "${M}│${N}  session    : ${D}${sess}…${N}\n"
            printf "${M}│${N}  prev agent : ${prev}\n"
            printf "${M}│${N}\n"
            printf "${M}│  ${U}HTTP headers on this call:${N}\n"
            printf "${M}│${N}    Authorization : Bearer ${tok_label}               ${D}← scoped JWT${N}\n"
            printf "${M}│${N}    X-SPIFFE-ID   : spiffe://…/request-manager       ${D}← SVID of caller${N}\n"
            printf "${M}│${N}    X-Delegation-User : spiffe://…/user/carlos@…    ${D}← OBO identity${N}\n"
            printf "${M}│${N}\n"
            printf "${M}│  ${U}Token ${tok_label} bound to:${N}\n"
            printf "${M}│${N}    aud : ${C}$(short_id "$target")${N}  ${D}← any other agent will REJECT this token${N}\n"
            printf "${M}│${N}    id  : ${D}${tid:0:36}…${N}\n"
            printf "${M}└─ awaiting response from agent-service…${N}\n"
            ;;

        # ── AGENT-SERVICE OPA CHECK ────────────────────────────────────────
        # Intentionally not rendered here — this event is already shown by
        # audit_tail (authz.allow with layer=defense-in-depth) which is the
        # authoritative DB record.  Rendering from both sources causes the
        # same check to appear twice per request.
        "Agent invocation authorized by OPA")
            ;;

        # ── SUCCESSFUL AGENT REPLY ─────────────────────────────────────────
        "Agent invocation successful")
            agent=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('agent_name',''))" 2>/dev/null)
            rlen=$(echo  "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('response_length',0))" 2>/dev/null)
            routed=$(echo "$line"| python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('has_routing',False))" 2>/dev/null)
            printf "\n${G}┌─ ✓ AGENT REPLIED  [${ts}]${N}\n"
            printf "${G}│${N}  agent    : ${W}${agent}${N}\n"
            printf "${G}│${N}  resp len : ${rlen} chars\n"
            printf "${G}│${N}  routed   : ${routed}  ${D}(true = sends back ROUTE: marker for next hop)${N}\n"
            printf "${G}└─ → response travels back to request-manager${N}\n"
            ;;

        # ── FINAL RESPONSE ─────────────────────────────────────────────────
        "Final response received")
            agent=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('agent_name',''))" 2>/dev/null)
            rlen=$(echo  "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('response_length',0))" 2>/dev/null)
            preview=$(docker exec partner-postgres-full psql -U user -d partner_agent -t -A \
                -c "SELECT LEFT(response_content,180) FROM request_logs
                    WHERE created_at >= '${MONITOR_START}'
                    ORDER BY created_at DESC LIMIT 1" \
                2>/dev/null | head -1)
            printf "\n${G}╔══ 💡 FINAL RESPONSE  [${ts}]${N}\n"
            printf "${G}║${N}  ${D}WRITTEN TO: PostgreSQL request_logs (audit trail)${N}\n"
            printf "${G}║${N}  ${D}RETURNED TO: browser via request-manager /adk/chat${N}\n"
            printf "${G}║${N}  📖 ${D}WHY: The specialist (${agent}) queried the RAG knowledge base for${N}\n"
            printf "${G}║${N}  ${D}     similar past support tickets, injected them into the LLM prompt,${N}\n"
            printf "${G}║${N}  ${D}     and called the LLM (Gemini/OpenAI/Ollama). The reply travelled back${N}\n"
            printf "${G}║${N}  ${D}     through request-manager which stored the full turn in PostgreSQL for${N}\n"
            printf "${G}║${N}  ${D}     auditing and returned it to the browser. The scoped tokens (Token₁,${N}\n"
            printf "${G}║${N}  ${D}     Token₂) are now expired or discarded — they were one-request-use.${N}\n"
            printf "${G}║${N}  ────────────────────────────────────────────────\n"
            printf "${G}║${N}  specialist : ${W}${agent}${N}\n"
            printf "${G}║${N}  length     : ${rlen} chars\n"
            printf "${G}║${N}  preview    : \"${W}$(trunc "$preview" 130)${N}\"\n"
            printf "${G}╚══════════════════════════════════════════════════${N}\n"
            ;;

        # ── ACCESS DENIED ──────────────────────────────────────────────────
        "AUTHORIZATION BLOCKED: OPA denied routing to agent")
            agent=$(echo  "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('requested_agent',''))" 2>/dev/null)
            depts=$(echo  "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('departments','[]'))" 2>/dev/null)
            reason=$(echo "$line" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('reason',''))" 2>/dev/null)
            printf "\n${R}╔══ ✗ ACCESS DENIED  [${ts}]${N}\n"
            printf "${R}║${N}  ${D}BLOCKED BY : OPA (Layer 1 in request-manager)${N}\n"
            printf "${R}║${N}  ${D}NO token exchange was performed — specialist never called${N}\n"
            printf "${R}║${N}  ────────────────────────────────────────────────\n"
            printf "${R}║${N}  blocked agent : ${agent}\n"
            printf "${R}║${N}  user depts    : ${depts}\n"
            printf "${R}║${N}  reason        : ${reason}\n"
            printf "${R}╚══ request returned to user with access-denied message${N}\n"
            ;;

        esac
    done
}

# ── request_logs tail (prompt + reply) ────────────────────────────────────────
prompt_reply_tail() {
    local last_id=0
    last_id=$(docker exec partner-postgres-full psql -U user -d partner_agent -t -A \
        -c "SELECT COALESCE(MAX(id),0) FROM request_logs" 2>/dev/null | tr -d '[:space:]')

    while true; do
        sleep 3
        # Process substitution + SOH delimiter (same fix as audit_tail).
        # Filter rows where completed_at IS NULL (request still in-flight) or
        # where content is empty — those produce blank cards.
        while IFS=$'\x01' read -r id ts agent req resp ms; do
            [ -z "$id" ] && continue
            [ -z "$ts" ] && continue      # skip in-flight rows
            [ -z "$req" ] && continue     # skip rows with no content yet
            last_id=$id
            printf "\n${W}╔══ 📋 REQUEST LOG  [${ts}]  ${D}(${ms}ms total)${N}\n"
            printf "${W}║${N}  ${D}STORED IN : PostgreSQL request_logs (full audit trail)${N}\n"
            printf "${W}║${N}  ${D}CONTAINS  : agent_id, prompt, response, processing_time_ms${N}\n"
            printf "${W}║${N}  📖 ${D}WHY: Every completed request is written to PostgreSQL so there is a${N}\n"
            printf "${W}║${N}  ${D}     permanent, tamper-evident record of who asked what, which agent${N}\n"
            printf "${W}║${N}  ${D}     answered, and how long it took. This table is the audit backbone.${N}\n"
            printf "${W}║${N}  ${D}     Compliance teams can query it to prove what the AI said and when.${N}\n"
            printf "${W}║${N}  ────────────────────────────────────────────────\n"
            printf "${W}║${N}  agent   : ${C}${agent}${N}\n"
            printf "${W}║${N}  prompt  : \"${Y}$(trunc "$req" 115)${N}\"\n"
            printf "${W}║${N}  reply   : \"${G}$(trunc "$resp" 175)${N}\"\n"
            printf "${W}╚══════════════════════════════════════════════════${N}\n"

        done < <(docker exec partner-postgres-full psql -U user -d partner_agent -t -A \
            -F$'\x01' \
            -c "SELECT id,
                       to_char(completed_at AT TIME ZONE 'UTC','HH24:MI:SS'),
                       agent_id,
                       LEFT(request_content,120),
                       LEFT(response_content,200),
                       processing_time_ms
                FROM request_logs
                WHERE id > ${last_id}
                  AND created_at  >= '${MONITOR_START}'
                  AND completed_at IS NOT NULL
                  AND request_content  IS NOT NULL
                  AND request_content  != ''
                ORDER BY id ASC" 2>/dev/null)
    done
}

# ── run ───────────────────────────────────────────────────────────────────────
# Capture start timestamp BEFORE printing the diagram so docker logs --since
# and DB queries both use the same cutoff — nothing older than this is shown.
MONITOR_START=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
export MONITOR_START

print_diagram

dcr_status() {
    # Show current DCR-registered clients from Keycloak every 10 seconds.
    # Runs once at startup to show the current state, then polls.
    local ADMIN_TOKEN=""
    while true; do
        sleep 10
        ADMIN_TOKEN=$(curl -sf -X POST \
            "http://localhost:8090/realms/master/protocol/openid-connect/token" \
            -H "Content-Type: application/x-www-form-urlencoded" \
            -d "client_id=admin-cli&grant_type=password&username=admin&password=admin123" \
            2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))" 2>/dev/null)
        [ -z "$ADMIN_TOKEN" ] && continue

        DCR_CLIENTS=$(curl -sf \
            -H "Authorization: Bearer ${ADMIN_TOKEN}" \
            "http://localhost:8090/admin/realms/partner-agent/clients?max=50" \
            2>/dev/null | python3 -c "
import sys, json, datetime
clients = json.load(sys.stdin)
dcr = []
for c in clients:
    name = c.get('name', '')
    if not ('spiffe://' in name or 'agent-service' in name or 'request-manager' in name):
        continue
    cid   = c.get('clientId', '')
    ctime = c.get('attributes', {}).get('client.secret.creation.time', '')
    sa    = 'yes' if c.get('serviceAccountsEnabled') else 'no'
    # Extract SPIFFE ID from name
    spiffe = ''
    if 'spiffe://' in name:
        start = name.find('spiffe://')
        end = name.find(')', start)
        spiffe = name[start:end] if end > 0 else name[start:start+60]
    service = name.split('(')[0].strip()
    # Format registration time
    reg_time = ''
    if ctime:
        try:
            reg_time = datetime.datetime.fromtimestamp(int(ctime)).strftime('%H:%M:%S')
        except Exception:
            reg_time = ctime
    dcr.append((cid[:8]+'…', service, spiffe, sa, reg_time))
for cid, svc, spiffe, sa, reg in dcr:
    print(f'entry:{cid}|{svc}|{spiffe}|sa={sa}|reg={reg}')
print(f'total:{len(dcr)}')
" 2>/dev/null)
        [ -z "$DCR_CLIENTS" ] && continue
        TOTAL=$(echo "$DCR_CLIENTS" | grep "^total:" | cut -d: -f2)
        [ -z "$TOTAL" ] || [ "$TOTAL" = "0" ] && continue

        TS=$(date +%H:%M:%S)
        printf "\n${G}┌─ 🔑 DCR STATUS  [${TS}]  ${D}(Keycloak self-registered clients — RFC 7591)${N}\n"
        printf "${G}│${N}  ${D}Each service registers itself on startup using the Initial Access Token.${N}\n"
        printf "${G}│${N}  ${D}DCR credentials are used as actor_token in RFC 8693 token exchanges.${N}\n"
        printf "${G}│${N}\n"
        echo "$DCR_CLIENTS" | grep "^entry:" | while IFS= read -r raw; do
            entry="${raw#entry:}"
            IFS='|' read -r cid svc spiffe sa reg <<< "$entry"
            printf "${G}│${N}  🔑 ${W}${cid}${N}  ${D}[${svc}]${N}\n"
            if [ -n "$spiffe" ]; then
                printf "${G}│${N}     SPIFFE : ${W}${spiffe}${N}\n"
            fi
            printf "${G}│${N}     service-account: ${sa}   registered-at: ${D}${reg}${N}\n"
            printf "${G}│${N}     role: ${D}client_credentials → DCR actor_token in token exchanges${N}\n"
            printf "${G}│${N}\n"
        done
        printf "${G}└─ ${TOTAL} service(s) self-registered — actor_tokens enable DCR identity in delegation chains${N}\n"
    done
}

if $DB_ONLY; then
    audit_tail &
    prompt_reply_tail &
    wait
else
    audit_tail &
    prompt_reply_tail &
    dcr_status &
    # --since prevents replaying the full container log history on every start.
    # Only lines emitted from this moment onwards are parsed.
    docker logs -f --since "$MONITOR_START" partner-request-manager-full 2>&1 | parse_logs &
    docker logs -f --since "$MONITOR_START" partner-agent-service-full   2>&1 | parse_logs &
    wait
fi
