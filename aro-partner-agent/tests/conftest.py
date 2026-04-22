"""Shared fixtures for aro-partner-agent tests."""

import pytest


@pytest.fixture
def mock_agent_config():
    """Sample ARO agent configuration dict."""
    return {
        "name": "aro-support",
        "description": "Azure infrastructure support agent — troubleshoots ARO, AKS, VMs, storage, networking, and telemetry issues",
        "departments": ["azure"],
        "llm_model": "gemini-2.5-flash",
        "system_message": "You are an Azure infrastructure support agent specializing in troubleshooting and diagnostics.",
        "sampling_params": {
            "strategy": {
                "type": "top_p",
                "temperature": 0.7,
                "top_p": 0.95,
            }
        },
        "mcp_servers": [
            {
                "name": "azure",
                "url": "http://azure-mcp-server:8080/mcp",
                "transport": "http",
            }
        ],
        "a2a": {
            "card_name": "ARO Support Agent",
            "card_description": "ARO infrastructure specialist.",
            "skills": [
                {
                    "id": "azure_troubleshooting",
                    "name": "Azure Troubleshooting",
                    "description": "Diagnoses Azure and ARO issues.",
                    "tags": ["azure", "aro"],
                    "examples": ["My pods on ARO keep getting OOMKilled"],
                },
            ],
        },
    }


@pytest.fixture
def mock_agent_config_no_mcp():
    """ARO agent configuration without MCP servers."""
    return {
        "name": "aro-support",
        "description": "Azure infrastructure support agent — troubleshoots ARO, AKS, VMs, storage, networking, and telemetry issues",
        "departments": ["azure"],
        "llm_model": "gemini-2.5-flash",
        "system_message": "You are an Azure infrastructure support agent specializing in troubleshooting and diagnostics.",
        "sampling_params": {
            "strategy": {
                "type": "top_p",
                "temperature": 0.7,
                "top_p": 0.95,
            }
        },
    }
