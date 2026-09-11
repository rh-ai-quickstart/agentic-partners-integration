package delegation_chain_validation

# Validate delegation chain structure
valid_delegation_chain if {
    chain := input.delegation.delegation_chain
    count(chain) > 0
    count(chain) <= 10  # Max depth
    not has_circular_delegation(chain)
}

has_circular_delegation(chain) if {
    # Check if any actor appears more than twice
    actor := chain[_]
    count([x | x := chain[_]; x == actor]) > 2
}

# Extract original user from chain
original_user := chain[count(chain) - 1] if {
    chain := input.delegation.delegation_chain
    count(chain) > 0
}

# Main authorization with delegation validation
allow if {
    intersection := input.user_departments & input.agent_capabilities
    count(intersection) > 0

    # If delegation exists, validate it
    input.delegation
    valid_delegation_chain
}
