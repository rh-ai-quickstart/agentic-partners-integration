package partner.authorization

# Auto-generated from agent YAML configs by scripts/sync_agent_capabilities.py.
# Do not edit manually — run: make sync-agents
#
# Agent capability mappings: which departments each agent can serve.
# The routing-agent can route to any department's specialist.
# Specialist agents are scoped to their specific department.
agent_capabilities := {
	"routing-agent": ["admin", "kubernetes", "network", "software"],
	"kubernetes-support": ["kubernetes"],
	"network-support": ["network"],
	"software-support": ["software"],
}

# Authoritative department allowlist.  Any department claimed by a
# DCR-registered agent outside this set is implicitly excluded.
_valid_departments := {"admin", "kubernetes", "network", "software"}

# Dynamic DCR-registered agent capabilities.
#
# Populated at runtime via OPA Data API when a new agent registers:
#   PUT /v1/data/agents/dynamic_capabilities
#   {"ericsson-k8s-agent": ["kubernetes"]}
#
# Only departments in _valid_departments are returned — departments outside
# the allowlist are stripped here at query time.
#
# NOTE: delegation.rego uses ``agent_capabilities`` (the static constant
# above) for authorization decisions.  This separate rule is used by the
# agent registry endpoint to expose all discovered agents (static + dynamic)
# without touching the authorization critical path.
dynamic_agent_capabilities[name] := filtered if {
	raw := data.agents.dynamic_capabilities[name]
	# Static agents are already covered by agent_capabilities — skip them
	not agent_capabilities[name]
	filtered := [d | d := raw[_]; _valid_departments[d]]
}
