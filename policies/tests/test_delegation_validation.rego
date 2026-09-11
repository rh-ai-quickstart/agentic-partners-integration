package delegation_chain_validation_test

import data.delegation_chain_validation
import rego.v1

# Test 1: Valid simple delegation - 2 hop chain
test_valid_simple_delegation if {
	result := delegation_chain_validation.valid_delegation_chain with input as {
		"delegation": {
			"delegation_chain": [
				"agent/software-support",
				"user/carlos",
			],
		},
	}
	result == true
}

# Test 2: Valid complex delegation - 5 hop chain
test_valid_complex_delegation if {
	result := delegation_chain_validation.valid_delegation_chain with input as {
		"delegation": {
			"delegation_chain": [
				"agent/routing-agent",
				"agent/kubernetes-support",
				"agent/network-support",
				"agent/software-support",
				"user/sharon",
			],
		},
	}
	result == true
}

# Test 3: Reject too deep - 11 hops (exceeds max depth of 10)
test_reject_too_deep if {
	result := delegation_chain_validation.valid_delegation_chain with input as {
		"delegation": {
			"delegation_chain": [
				"agent/routing-agent",
				"agent/kubernetes-support",
				"agent/network-support",
				"agent/software-support",
				"agent/routing-agent-2",
				"agent/kubernetes-support-2",
				"agent/network-support-2",
				"agent/software-support-2",
				"agent/routing-agent-3",
				"agent/kubernetes-support-3",
				"user/carlos",
			],
		},
	}
	result == false
}

# Test 4: Reject circular - routing->k8s->routing->k8s->routing (routing appears 3 times)
test_reject_circular if {
	result := delegation_chain_validation.valid_delegation_chain with input as {
		"delegation": {
			"delegation_chain": [
				"agent/routing-agent",
				"agent/kubernetes-support",
				"agent/routing-agent",
				"agent/kubernetes-support",
				"agent/routing-agent",
			],
		},
	}
	result == false
}

# Test 5: Allow bidirectional - routing->k8s->routing (each appears max 2 times, OK)
test_allow_bidirectional if {
	result := delegation_chain_validation.valid_delegation_chain with input as {
		"delegation": {
			"delegation_chain": [
				"agent/routing-agent",
				"agent/kubernetes-support",
				"agent/routing-agent",
			],
		},
	}
	result == true
}

# Test 6: Extract original user - get correct user from end of chain
test_extract_original_user if {
	result := delegation_chain_validation.original_user with input as {
		"delegation": {
			"delegation_chain": [
				"agent/routing-agent",
				"agent/kubernetes-support",
				"agent/software-support",
				"user/carlos",
			],
		},
	}
	result == "user/carlos"
}

# Test 6b: Extract original user from simple chain
test_extract_original_user_simple if {
	result := delegation_chain_validation.original_user with input as {
		"delegation": {
			"delegation_chain": [
				"agent/software-support",
				"user/sharon",
			],
		},
	}
	result == "user/sharon"
}

# Test 6c: Extract original user from single element chain
test_extract_original_user_single if {
	result := delegation_chain_validation.original_user with input as {
		"delegation": {
			"delegation_chain": [
				"user/luis",
			],
		},
	}
	result == "user/luis"
}

# Test 7: Full authorization with valid delegation chain
test_allow_with_valid_chain if {
	result := delegation_chain_validation.allow with input as {
		"user_departments": ["software", "kubernetes"],
		"agent_capabilities": ["kubernetes", "network"],
		"delegation": {
			"delegation_chain": [
				"agent/network-support",
				"agent/kubernetes-support",
				"user/carlos",
			],
		},
	}
	result == true
}

# Test 7b: Deny authorization with valid chain but no department overlap
test_deny_with_valid_chain_no_overlap if {
	result := delegation_chain_validation.allow with input as {
		"user_departments": ["software"],
		"agent_capabilities": ["kubernetes", "network"],
		"delegation": {
			"delegation_chain": [
				"agent/network-support",
				"agent/kubernetes-support",
				"user/carlos",
			],
		},
	}
	result == false
}

# Test 7c: Deny authorization with invalid chain (too deep)
test_deny_with_invalid_chain if {
	result := delegation_chain_validation.allow with input as {
		"user_departments": ["software", "kubernetes"],
		"agent_capabilities": ["kubernetes", "network"],
		"delegation": {
			"delegation_chain": [
				"agent/agent-1",
				"agent/agent-2",
				"agent/agent-3",
				"agent/agent-4",
				"agent/agent-5",
				"agent/agent-6",
				"agent/agent-7",
				"agent/agent-8",
				"agent/agent-9",
				"agent/agent-10",
				"user/carlos",
			],
		},
	}
	result == false
}

# Test 7d: Deny authorization with circular chain
test_deny_with_circular_chain if {
	result := delegation_chain_validation.allow with input as {
		"user_departments": ["software", "kubernetes"],
		"agent_capabilities": ["kubernetes", "network"],
		"delegation": {
			"delegation_chain": [
				"agent/routing-agent",
				"agent/kubernetes-support",
				"agent/routing-agent",
				"agent/kubernetes-support",
				"agent/routing-agent",
			],
		},
	}
	result == false
}

# Test: Empty chain should be rejected
test_reject_empty_chain if {
	result := delegation_chain_validation.valid_delegation_chain with input as {
		"delegation": {
			"delegation_chain": [],
		},
	}
	result == false
}

# Test: Single hop chain should be valid
test_valid_single_hop if {
	result := delegation_chain_validation.valid_delegation_chain with input as {
		"delegation": {
			"delegation_chain": [
				"user/sharon",
			],
		},
	}
	result == true
}

# Test: Maximum allowed depth (10 hops) should be valid
test_valid_max_depth if {
	result := delegation_chain_validation.valid_delegation_chain with input as {
		"delegation": {
			"delegation_chain": [
				"agent/agent-1",
				"agent/agent-2",
				"agent/agent-3",
				"agent/agent-4",
				"agent/agent-5",
				"agent/agent-6",
				"agent/agent-7",
				"agent/agent-8",
				"agent/agent-9",
				"user/carlos",
			],
		},
	}
	result == true
}

# Test: Circular detection - same actor appearing exactly 3 times
test_circular_three_occurrences if {
	has_circular := delegation_chain_validation.has_circular_delegation([
		"agent/software-support",
		"agent/network-support",
		"agent/software-support",
		"agent/kubernetes-support",
		"agent/software-support",
	])
	has_circular == true
}

# Test: Non-circular - same actor appearing exactly 2 times
test_not_circular_two_occurrences if {
	has_circular := delegation_chain_validation.has_circular_delegation([
		"agent/software-support",
		"agent/network-support",
		"agent/software-support",
	])
	has_circular == false
}

# Test: Non-circular - all unique actors
test_not_circular_all_unique if {
	has_circular := delegation_chain_validation.has_circular_delegation([
		"agent/routing-agent",
		"agent/kubernetes-support",
		"agent/network-support",
		"user/carlos",
	])
	has_circular == false
}
