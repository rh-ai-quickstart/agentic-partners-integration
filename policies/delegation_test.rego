package partner.authorization_test

import data.partner.authorization
import rego.v1

# Test: Service-to-service calls are allowed
test_service_to_service_allowed if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "software-support",
	}
	result.allow == true
}

# Test: Delegated access — Carlos (software dept) can use software-support
test_delegation_carlos_software_support if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "software-support",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/carlos",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/software-support",
			"user_departments": ["engineering", "software"],
		},
	}
	result.allow == true
	"software" in result.effective_departments
}

# Test: Delegated access — Carlos (kubernetes dept) can use kubernetes-support
test_delegation_carlos_kubernetes_support if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "kubernetes-support",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/carlos",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/kubernetes-support",
			"user_departments": ["engineering", "software", "kubernetes"],
		},
	}
	result.allow == true
	"kubernetes" in result.effective_departments
}

# Test: Delegated access denied — Carlos cannot use network-support
test_delegation_carlos_network_denied if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "network-support",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/carlos",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/network-support",
			"user_departments": ["engineering", "software"],
		},
	}
	result.allow == false
}

# Test: Delegated access — Sharon (all depts) can use any agent
test_delegation_sharon_all_agents if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "network-support",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/sharon",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/network-support",
			"user_departments": ["engineering", "software", "network", "admin"],
		},
	}
	result.allow == true
	"network" in result.effective_departments
}

# Test: Delegated access denied — Josh (no depts) is denied everything
test_delegation_josh_denied if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "software-support",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/josh",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/software-support",
			"user_departments": [],
		},
	}
	result.allow == false
}

# Test: Autonomous agent access — denied without delegation
test_autonomous_agent_denied if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/agent/software-support",
		"agent_name": "software-support",
	}
	result.allow == false
	contains(result.reason, "delegation context required")
}

# Test: Unknown agent — denied
test_unknown_agent_denied if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "nonexistent-agent",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/sharon",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/nonexistent-agent",
			"user_departments": ["engineering", "software", "network", "admin"],
		},
	}
	result.allow == false
	contains(result.reason, "Unknown agent")
}

# Test: Luis (network dept) can use network-support
test_delegation_luis_network_support if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "network-support",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/luis",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/network-support",
			"user_departments": ["engineering", "network"],
		},
	}
	result.allow == true
	"network" in result.effective_departments
}

# Test: Luis (network dept) cannot use software-support
test_delegation_luis_software_denied if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "software-support",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/luis",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/software-support",
			"user_departments": ["engineering", "network"],
		},
	}
	result.allow == false
}

# Test: Delegated access — Carlos (azure dept) can use aro-support
test_delegation_carlos_aro_support if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "aro-support",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/carlos",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/aro-support",
			"user_departments": ["engineering", "software", "kubernetes", "azure"],
		},
	}
	result.allow == true
	"azure" in result.effective_departments
}

# Test: Delegated access denied — Luis cannot use aro-support
test_delegation_luis_aro_denied if {
	result := authorization.decision with input as {
		"caller_spiffe_id": "spiffe://partner.example.com/service/request-manager",
		"agent_name": "aro-support",
		"delegation": {
			"user_spiffe_id": "spiffe://partner.example.com/user/luis",
			"agent_spiffe_id": "spiffe://partner.example.com/agent/aro-support",
			"user_departments": ["engineering", "network"],
		},
	}
	result.allow == false
}
