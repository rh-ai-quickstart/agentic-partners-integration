package partner.authorization

import rego.v1

# Parse entity type from SPIFFE ID: spiffe://domain/TYPE/name -> TYPE
parse_spiffe_type(spiffe_id) := type if {
	parts := split(spiffe_id, "/")
	count(parts) >= 4
	type := parts[count(parts) - 2]
}

# Parse entity name from SPIFFE ID: spiffe://domain/type/NAME -> NAME
parse_spiffe_name(spiffe_id) := name if {
	parts := split(spiffe_id, "/")
	count(parts) >= 4
	name := parts[count(parts) - 1]
}

# Default deny
default decision := {
	"allow": false,
	"reason": "No matching policy rule",
	"effective_departments": [],
}

# Rule 1: Service-to-service calls WITHOUT delegation are always allowed.
# Infrastructure services (request-manager, etc.) call each other freely.
# When a delegation context is present, Rules 3/4 handle the permission check.
decision := result if {
	caller_type := parse_spiffe_type(input.caller_spiffe_id)
	caller_type == "service"
	not input.delegation
	result := {
		"allow": true,
		"reason": "Service-to-service call allowed",
		"effective_departments": [],
	}
}

# Rule 2: Direct user access — users can invoke agents through delegation.
# The actual permission check happens via the delegation context.
decision := result if {
	caller_type := parse_spiffe_type(input.caller_spiffe_id)
	caller_type == "user"
	result := {
		"allow": true,
		"reason": "Direct user access allowed",
		"effective_departments": [],
	}
}

# Rule 3: Delegated agent access — compute permission intersection.
# Effective Permissions = User Departments ∩ Agent Capabilities
decision := result if {
	caller_type := parse_spiffe_type(input.caller_spiffe_id)
	caller_type == "service"
	input.delegation
	input.agent_name

	# Get user departments from delegation context or fallback
	user_depts := _resolve_user_departments

	# Get agent capabilities
	agent_name := input.agent_name
	agent_caps := agent_capabilities[agent_name]

	# Compute intersection
	effective := {d | some d in user_depts; d in agent_caps}

	count(effective) > 0

	result := {
		"allow": true,
		"reason": sprintf("Delegated access granted — effective departments: %v", [effective]),
		"effective_departments": effective,
	}
}

# Rule 4: Delegated agent access denied — empty intersection.
decision := result if {
	caller_type := parse_spiffe_type(input.caller_spiffe_id)
	caller_type == "service"
	input.delegation
	input.agent_name

	user_depts := _resolve_user_departments
	agent_name := input.agent_name
	agent_caps := agent_capabilities[agent_name]

	effective := {d | some d in user_depts; d in agent_caps}

	count(effective) == 0

	result := {
		"allow": false,
		"reason": sprintf("No overlapping departments between user %v and agent %v capabilities %v", [user_depts, agent_name, agent_caps]),
		"effective_departments": [],
	}
}

# Rule 5: Agent without delegation — always denied.
# Agents cannot act autonomously; they require user delegation context.
decision := result if {
	caller_type := parse_spiffe_type(input.caller_spiffe_id)
	caller_type == "agent"
	not input.delegation
	result := {
		"allow": false,
		"reason": "Autonomous agent access denied — delegation context required",
		"effective_departments": [],
	}
}

# Rule 6: Unknown agent — agent_name not in capabilities map.
decision := result if {
	input.agent_name
	input.delegation
	not agent_capabilities[input.agent_name]
	result := {
		"allow": false,
		"reason": sprintf("Unknown agent: %v", [input.agent_name]),
		"effective_departments": [],
	}
}

# Helper: resolve user departments from delegation context or fallback map.
_resolve_user_departments := depts if {
	# Prefer departments from delegation context (from JWT claims or DB)
	input.delegation.user_departments
	count(input.delegation.user_departments) > 0
	depts := input.delegation.user_departments
} else := depts if {
	# Fall back to user email lookup in static map
	user_name := parse_spiffe_name(input.delegation.user_spiffe_id)
	email := sprintf("%v@example.com", [user_name])
	depts := user_departments_fallback[email]
} else := []
