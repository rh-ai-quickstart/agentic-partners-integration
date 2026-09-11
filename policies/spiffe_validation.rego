package spiffe

import future.keywords.if
import future.keywords.in

# Valid SPIFFE IDs in our trust domain
valid_services := {
    "spiffe://partner.example.com/request-manager",
    "spiffe://partner.example.com/agent-service",
    "spiffe://partner.example.com/kubernetes-agent",
    "spiffe://partner.example.com/aro-agent",
    "spiffe://partner.example.com/rag-api"
}

# Validate SPIFFE ID format and trust domain
is_valid_spiffe_id(spiffe_id) if {
    startswith(spiffe_id, "spiffe://partner.example.com/")
    spiffe_id in valid_services
}

# Allow if SPIFFE ID is valid
allow if {
    input.spiffe_id
    is_valid_spiffe_id(input.spiffe_id)
}

# Deny with reason if SPIFFE ID is invalid
deny contains reason if {
    input.spiffe_id
    not is_valid_spiffe_id(input.spiffe_id)
    reason := sprintf("Invalid SPIFFE ID: %s not in registered services", [input.spiffe_id])
}

deny contains reason if {
    not input.spiffe_id
    reason := "No SPIFFE ID provided in request"
}
