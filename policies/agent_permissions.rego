package partner.authorization

# Agent capability mappings: which departments each agent can serve.
# The routing-agent can route to any department's specialist.
# Specialist agents are scoped to their specific department.
agent_capabilities := {
	"routing-agent": ["software", "network", "admin"],
	"software-support": ["software"],
	"network-support": ["network"],
}
