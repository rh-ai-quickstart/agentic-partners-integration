#!/usr/bin/env python3
"""Generate Praxis Policy Engine agent_capabilities.yaml from agent YAML configs.

Reads each agent YAML in agent-service/config/agents/ and produces
a YAML file with agent_capabilities derived from the 'departments'
field in each config.  The routing-agent entry is auto-generated
as the union of all specialist departments plus 'admin'.

Usage:
    python policies/sync_agent_capabilities.py
"""

import sys
from pathlib import Path

import yaml

AGENT_CONFIG_DIR = Path(__file__).parent.parent / "agent-service" / "config" / "agents"
OUTPUT_FILE = Path(__file__).parent / "agent_capabilities.yaml"


def load_agent_configs(config_dir: Path) -> dict[str, list[str]]:
    """Load departments from all agent YAML configs."""
    capabilities: dict[str, list[str]] = {}
    for yaml_file in sorted(config_dir.glob("*.yaml")):
        with open(yaml_file) as f:
            config = yaml.safe_load(f) or {}
        name = config.get("name")
        departments = config.get("departments")
        if name and departments:
            capabilities[name] = departments
    return capabilities


def generate_yaml(specialist_capabilities: dict[str, list[str]]) -> str:
    """Generate the agent_capabilities.yaml content."""
    # routing-agent gets the union of all specialist departments + admin
    all_departments = set()
    for depts in specialist_capabilities.values():
        all_departments.update(depts)
    all_departments.add("admin")

    # Build the data structure
    agent_capabilities: dict[str, list[str]] = {}

    # routing-agent first
    agent_capabilities["routing-agent"] = sorted(all_departments)

    # Specialist agents
    for name, depts in sorted(specialist_capabilities.items()):
        agent_capabilities[name] = depts

    data = {
        "data": {
            "agent_capabilities": agent_capabilities,
            "valid_departments": sorted(all_departments),
            "service_names": [
                "request-manager",
                "agent-service",
                "kubernetes-agent",
                "aro-agent",
                "rag-api",
            ],
            "trust_domain": "partner.example.com",
        }
    }

    header = (
        "# Auto-generated from agent YAML configs by policies/sync_agent_capabilities.py\n"
        "# Do not edit manually — run: make sync-agents\n"
        "#\n"
        "# Praxis requires a top-level `data:` key for attribute files.\n"
        "# policy_client.py reads from `data.agent_capabilities` (or falls back to flat format).\n"
    )

    return header + yaml.dump(data, default_flow_style=False, sort_keys=False)


def main() -> None:
    if not AGENT_CONFIG_DIR.is_dir():
        print(
            f"ERROR: Agent config directory not found: {AGENT_CONFIG_DIR}",
            file=sys.stderr,
        )
        sys.exit(1)

    capabilities = load_agent_configs(AGENT_CONFIG_DIR)
    if not capabilities:
        print("ERROR: No agent configs with departments found", file=sys.stderr)
        sys.exit(1)

    yaml_content = generate_yaml(capabilities)
    OUTPUT_FILE.write_text(yaml_content)
    print(f"Generated {OUTPUT_FILE} with {len(capabilities)} specialist agent(s):")
    for name, depts in sorted(capabilities.items()):
        print(f"  {name}: {depts}")


if __name__ == "__main__":
    main()
