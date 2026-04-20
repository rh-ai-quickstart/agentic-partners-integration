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
