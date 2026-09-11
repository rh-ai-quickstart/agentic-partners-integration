#!/usr/bin/env python3
"""Generate OPA agent_permissions.rego from agent YAML configs.

Reads each agent YAML in agent-service/config/agents/ and produces
a Rego file with agent_capabilities derived from the 'departments'
field in each config.  The routing-agent entry is auto-generated
as the union of all specialist departments plus 'admin'.

Usage:
    python scripts/sync_agent_capabilities.py
"""

import sys
from pathlib import Path

import yaml

AGENT_CONFIG_DIR = Path(__file__).parent.parent / "agent-service" / "config" / "agents"
OUTPUT_FILE = Path(__file__).parent.parent / "policies" / "agent_permissions.rego"


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


def generate_rego(specialist_capabilities: dict[str, list[str]]) -> str:
    """Generate the agent_permissions.rego content."""
    # routing-agent gets the union of all specialist departments + admin
    all_departments = set()
    for depts in specialist_capabilities.values():
        all_departments.update(depts)
    all_departments.add("admin")

    lines = [
        "package partner.authorization",
        "",
        "# Auto-generated from agent YAML configs by scripts/sync_agent_capabilities.py.",
        "# Do not edit manually — run: make sync-agents",
        "#",
        "# Agent capability mappings: which departments each agent can serve.",
        "# The routing-agent can route to any department's specialist.",
        "# Specialist agents are scoped to their specific department.",
        "agent_capabilities := {",
    ]

    # routing-agent first
    routing_depts = ", ".join(f'"{d}"' for d in sorted(all_departments))
    lines.append(f'\t"routing-agent": [{routing_depts}],')

    # Specialist agents
    for name, depts in sorted(specialist_capabilities.items()):
        dept_str = ", ".join(f'"{d}"' for d in depts)
        lines.append(f'\t"{name}": [{dept_str}],')

    lines.append("}")
    lines.append("")  # trailing newline
    return "\n".join(lines)


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

    rego_content = generate_rego(capabilities)
    OUTPUT_FILE.write_text(rego_content)
    print(f"Generated {OUTPUT_FILE} with {len(capabilities)} specialist agent(s):")
    for name, depts in sorted(capabilities.items()):
        print(f"  {name}: {depts}")


if __name__ == "__main__":
    main()
