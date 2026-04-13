"""A2A Agent Card definitions for the partner specialist agents."""

from a2a.types import AgentCapabilities, AgentCard, AgentSkill


def create_software_support_card(base_url: str) -> AgentCard:
    """Create an A2A AgentCard for the Software Support agent."""
    return AgentCard(
        name="Software Support Agent",
        description=(
            "Software support specialist that helps users resolve software issues "
            "including bugs, crashes, errors, installation problems, and application "
            "failures. Uses a RAG-backed knowledge base of support tickets to provide "
            "grounded troubleshooting guidance with structured analysis, research, "
            "and solution phases."
        ),
        url=base_url,
        protocol_version="0.3.0",
        version="0.1.0",
        preferred_transport="JSONRPC",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            state_transition_history=True,
        ),
        skills=[
            AgentSkill(
                id="software_troubleshooting",
                name="Software Troubleshooting",
                description=(
                    "Diagnoses and resolves software issues including application crashes, "
                    "error codes, installation failures, configuration problems, and "
                    "unexpected behavior. Searches a knowledge base of historical support "
                    "tickets for similar issues and proven solutions."
                ),
                tags=["software", "bugs", "errors", "crashes", "troubleshooting"],
                examples=[
                    "My application keeps crashing with a segmentation fault",
                    "I'm getting error code 0x80070005 during installation",
                    "The software won't start after the latest update",
                    "How do I fix a dependency conflict in my Python environment?",
                    "Application hangs when processing large files",
                ],
            ),
            AgentSkill(
                id="software_installation",
                name="Software Installation & Configuration",
                description=(
                    "Assists with software installation, setup, and configuration issues. "
                    "Provides step-by-step guidance for resolving installation blockers, "
                    "dependency issues, and environment setup problems."
                ),
                tags=["installation", "configuration", "setup", "dependencies"],
                examples=[
                    "How do I install this package on Ubuntu?",
                    "Installation fails with missing dependency errors",
                    "How do I configure the application for production use?",
                ],
            ),
        ],
    )


def create_network_support_card(base_url: str) -> AgentCard:
    """Create an A2A AgentCard for the Network Support agent."""
    return AgentCard(
        name="Network Support Agent",
        description=(
            "Network support specialist that helps users resolve network issues "
            "including VPN connectivity, DNS resolution, firewall configuration, "
            "routing problems, and general connectivity failures. Uses a RAG-backed "
            "knowledge base of network support tickets to provide grounded "
            "troubleshooting with systematic diagnostic approaches."
        ),
        url=base_url,
        protocol_version="0.3.0",
        version="0.1.0",
        preferred_transport="JSONRPC",
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            state_transition_history=True,
        ),
        skills=[
            AgentSkill(
                id="network_diagnostics",
                name="Network Diagnostics & Troubleshooting",
                description=(
                    "Diagnoses and resolves network connectivity issues including VPN "
                    "failures, DNS resolution problems, firewall misconfigurations, "
                    "routing issues, and intermittent connectivity drops. Provides "
                    "systematic diagnostic commands and step-by-step resolution guides."
                ),
                tags=["network", "connectivity", "VPN", "DNS", "firewall", "routing"],
                examples=[
                    "My VPN connection keeps dropping every few minutes",
                    "DNS resolution is failing for internal domains",
                    "I can't access the application behind the firewall",
                    "Network latency is extremely high between two sites",
                    "How do I troubleshoot a routing loop?",
                ],
            ),
            AgentSkill(
                id="network_configuration",
                name="Network Configuration",
                description=(
                    "Assists with network configuration tasks including firewall rules, "
                    "VPN setup, DNS configuration, and network topology analysis."
                ),
                tags=["configuration", "firewall-rules", "VPN-setup", "DNS-config"],
                examples=[
                    "How do I configure a site-to-site VPN?",
                    "What firewall rules do I need to allow HTTPS traffic?",
                    "How do I set up DNS forwarding?",
                ],
            ),
        ],
    )
