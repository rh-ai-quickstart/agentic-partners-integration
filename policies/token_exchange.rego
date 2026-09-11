package token_exchange

import future.keywords.if
import future.keywords.in
import data.spiffe

# Agent to allowed actions mapping
agent_permissions := {
    "spiffe://partner.example.com/request-manager": {
        "allowed_actions": ["get_nodes", "get_pods", "describe_cluster", "list_namespaces", "get_events"],
        "allowed_audiences": ["kubernetes", "openshift"]
    },
    "spiffe://partner.example.com/agent-service": {
        "allowed_actions": ["query_knowledge", "answer_question"],
        "allowed_audiences": ["internal"]
    },
    "spiffe://partner.example.com/kubernetes-agent": {
        "allowed_actions": ["get_nodes", "get_pods", "list_namespaces"],
        "allowed_audiences": ["kubernetes"]
    }
}

# Check if token exchange is allowed
allow if {
    # 1. Validate SPIFFE ID exists and is valid
    input.spiffe_id
    spiffe.is_valid_spiffe_id(input.spiffe_id)

    # 2. Verify agent has permissions for requested action
    action_allowed

    # 3. Verify requested audience is valid for this agent
    audience_allowed

    # 4. Rate limiting check (simplified - in prod use external cache)
    within_rate_limit
}

action_allowed if {
    permissions := agent_permissions[input.spiffe_id]
    input.requested_action in permissions.allowed_actions
}

audience_allowed if {
    permissions := agent_permissions[input.spiffe_id]
    input.audience in permissions.allowed_audiences
}

# Simplified rate limiting (in production, query external cache/DB)
within_rate_limit if {
    # For now, always allow - implement with Redis/memcached in production
    true
}

# Deny with detailed reasons
deny contains reason if {
    input.spiffe_id
    not spiffe.is_valid_spiffe_id(input.spiffe_id)
    reason := sprintf("SPIFFE ID %s is not valid", [input.spiffe_id])
}

deny contains reason if {
    input.spiffe_id
    spiffe.is_valid_spiffe_id(input.spiffe_id)
    not action_allowed
    reason := sprintf("Action '%s' not allowed for %s", [input.requested_action, input.spiffe_id])
}

deny contains reason if {
    input.spiffe_id
    spiffe.is_valid_spiffe_id(input.spiffe_id)
    not audience_allowed
    reason := sprintf("Audience '%s' not allowed for %s", [input.audience, input.spiffe_id])
}
