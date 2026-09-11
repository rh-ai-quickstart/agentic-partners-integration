"""
Delegation chain utilities for actor-actor (act) claims.

Provides utilities to build, parse, and validate nested delegation chains
used in JWT actor claims (RFC 8693-style act claims). Delegation chains track
the sequence of actors (users, agents, services) in a request.

Example chain structure:
{
    "sub": "routing-agent",
    "act": {
        "sub": "k8s-agent",
        "act": {
            "sub": "carlos@example.com"
        }
    }
}

This represents: routing-agent acting on behalf of k8s-agent acting on behalf of carlos@example.com
Flattened: ["routing-agent", "k8s-agent", "carlos@example.com"]
"""

from typing import List, Optional


def parse_act_claim(act_claim: dict) -> List[str]:
    """
    Recursively parse nested act claims into a flat list of actors.

    Traverses the nested act claim structure from root to leaf, collecting
    all actors in the delegation chain. The first element is the current
    actor (root), and the last element is the original user (leaf).

    Args:
        act_claim: Nested dictionary with 'sub' (subject) and optional 'act' (actor) keys

    Returns:
        Flat list of actor identifiers from current to original
        Example: ["routing-agent", "k8s-agent", "carlos@example.com"]

    Raises:
        ValueError: If act_claim is missing required 'sub' field
        ValueError: If act_claim contains circular references (detected during validation)

    Example:
        >>> claim = {
        ...     "sub": "routing-agent",
        ...     "act": {
        ...         "sub": "k8s-agent",
        ...         "act": {"sub": "carlos@example.com"}
        ...     }
        ... }
        >>> parse_act_claim(claim)
        ["routing-agent", "k8s-agent", "carlos@example.com"]
    """
    if not isinstance(act_claim, dict):
        raise ValueError("act_claim must be a dictionary")

    if "sub" not in act_claim:
        raise ValueError("act_claim must contain 'sub' field")

    chain = [act_claim["sub"]]

    # Recursively traverse the act chain
    current = act_claim
    while "act" in current and current["act"]:
        next_claim = current["act"]

        if not isinstance(next_claim, dict):
            raise ValueError("act field must be a dictionary")

        if "sub" not in next_claim:
            raise ValueError("nested act claim must contain 'sub' field")

        chain.append(next_claim["sub"])
        current = next_claim

    return chain


def build_act_claim(current_actor: str, previous_act: Optional[dict] = None) -> dict:
    """
    Build a nested act claim structure from current actor and previous claim.

    Creates a new delegation level by wrapping the previous act claim.
    Used when an agent/service acts on behalf of another actor.

    Args:
        current_actor: Identity of the current actor (e.g., "routing-agent")
        previous_act: Optional previous act claim to nest (None for leaf/original user)

    Returns:
        Nested act claim dictionary with structure {"sub": current_actor, "act": previous_act}
        If previous_act is None, returns {"sub": current_actor}

    Example:
        >>> user_claim = build_act_claim("carlos@example.com")
        >>> user_claim
        {"sub": "carlos@example.com"}
        >>> agent_claim = build_act_claim("k8s-agent", user_claim)
        >>> agent_claim
        {"sub": "k8s-agent", "act": {"sub": "carlos@example.com"}}
        >>> routing_claim = build_act_claim("routing-agent", agent_claim)
        >>> routing_claim
        {"sub": "routing-agent", "act": {"sub": "k8s-agent", "act": {"sub": "carlos@example.com"}}}
    """
    if not current_actor or not isinstance(current_actor, str):
        raise ValueError("current_actor must be a non-empty string")

    claim = {"sub": current_actor}

    if previous_act is not None:
        if not isinstance(previous_act, dict):
            raise ValueError("previous_act must be a dictionary or None")
        claim["act"] = previous_act

    return claim


def validate_delegation_chain(chain: List[str]) -> bool:
    """
    Validate a delegation chain for depth and circularity constraints.

    Checks that the chain is not too deep (to prevent abuse) and does not
    contain circular references (same actor appearing too many times).

    Constraints:
        - Max depth: 10 hops (prevents excessive delegation)
        - Max occurrences: Any single actor can appear at most 2 times
          (allows one re-delegation but prevents cycles)

    Args:
        chain: Flat list of actor identifiers

    Returns:
        True if chain is valid, False otherwise

    Example:
        >>> validate_delegation_chain(["routing-agent", "k8s-agent", "user@example.com"])
        True
        >>> validate_delegation_chain(["a"] * 11)  # Too deep
        False
        >>> validate_delegation_chain(["agent1", "agent2", "agent1", "agent2", "agent1"])  # Circular
        False
    """
    if not isinstance(chain, list):
        return False

    # Check max depth (10 hops)
    if len(chain) > 10:
        return False

    # Check for empty chain
    if len(chain) == 0:
        return False

    # Check for circularity: no actor should appear more than 2 times
    actor_counts = {}
    for actor in chain:
        if not isinstance(actor, str) or not actor:
            return False
        actor_counts[actor] = actor_counts.get(actor, 0) + 1
        if actor_counts[actor] > 2:
            return False

    return True


def extract_original_user(act_claim: dict) -> str:
    """
    Extract the original user (leaf actor) from a nested act claim.

    Traverses to the bottom of the delegation chain to find the user who
    originally initiated the request.

    Args:
        act_claim: Nested act claim dictionary

    Returns:
        The original user's identifier (last 'sub' in the chain)

    Raises:
        ValueError: If act_claim is invalid or missing required fields

    Example:
        >>> claim = {
        ...     "sub": "routing-agent",
        ...     "act": {
        ...         "sub": "k8s-agent",
        ...         "act": {"sub": "carlos@example.com"}
        ...     }
        ... }
        >>> extract_original_user(claim)
        "carlos@example.com"
        >>> extract_original_user({"sub": "direct-user"})
        "direct-user"
    """
    chain = parse_act_claim(act_claim)
    return chain[-1]  # Return the last (leaf) actor
