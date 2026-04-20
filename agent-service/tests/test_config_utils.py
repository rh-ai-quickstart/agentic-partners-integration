"""Tests for agent_service.config_utils."""

import pytest
from agent_service.config_utils import (
    load_config_from_path,
    load_yaml,
    resolve_agent_service_path,
)


class TestLoadYaml:
    """Tests for load_yaml()."""

    def test_valid_yaml(self, tmp_path):
        yaml_file = tmp_path / "test.yaml"
        yaml_file.write_text("key1: value1\nkey2: 42\n")
        result = load_yaml(str(yaml_file))
        assert result == {"key1": "value1", "key2": 42}

    def test_empty_file(self, tmp_path):
        yaml_file = tmp_path / "empty.yaml"
        yaml_file.write_text("")
        result = load_yaml(str(yaml_file))
        assert result == {}

    def test_yaml_list_returns_empty_dict(self, tmp_path):
        """A YAML file containing a list (not dict) should return {}."""
        yaml_file = tmp_path / "list.yaml"
        yaml_file.write_text("- item1\n- item2\n")
        result = load_yaml(str(yaml_file))
        assert result == {}

    def test_nested_yaml(self, tmp_path):
        yaml_file = tmp_path / "nested.yaml"
        yaml_file.write_text("parent:\n  child: value\n")
        result = load_yaml(str(yaml_file))
        assert result == {"parent": {"child": "value"}}


class TestResolveAgentServicePath:
    """Tests for resolve_agent_service_path()."""

    def test_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="Could not find"):
            resolve_agent_service_path("nonexistent_path_xyz_12345")

    def test_resolves_existing_path(self, tmp_path, monkeypatch):
        """Verify resolution works when the path exists at cwd fallback."""
        target = tmp_path / "my_config"
        target.mkdir()
        monkeypatch.chdir(tmp_path)
        # The function tries Path(".") / relative_path as a fallback
        result = resolve_agent_service_path("my_config")
        assert result.exists()


class TestLoadConfigFromPath:
    """Tests for load_config_from_path()."""

    def test_loads_and_combines_configs(self, tmp_path):
        # Create config.yaml
        config_file = tmp_path / "config.yaml"
        config_file.write_text("llm_backend: openai\nllm_model: gpt-4\n")

        # Create additional YAML
        extra_file = tmp_path / "extra.yaml"
        extra_file.write_text("rag_endpoint: http://localhost:8080\n")

        # Create agents directory with agent files
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        agent_file = agents_dir / "routing-agent.yaml"
        agent_file.write_text("name: routing-agent\nsystem_message: Route\n")

        result = load_config_from_path(tmp_path)

        assert result["llm_backend"] == "openai"
        assert result["llm_model"] == "gpt-4"
        assert result["rag_endpoint"] == "http://localhost:8080"
        assert len(result["agents"]) == 1
        assert result["agents"][0]["name"] == "routing-agent"

    def test_no_config_yaml(self, tmp_path):
        """When config.yaml doesn't exist, only other yamls and agents load."""
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        result = load_config_from_path(tmp_path)
        assert result["agents"] == []

    def test_multiple_agents(self, tmp_path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        (agents_dir / "a.yaml").write_text("name: agent-a\n")
        (agents_dir / "b.yaml").write_text("name: agent-b\n")
        (tmp_path / "config.yaml").write_text("llm_backend: gemini\n")

        result = load_config_from_path(tmp_path)
        assert len(result["agents"]) == 2
        names = {a["name"] for a in result["agents"]}
        assert names == {"agent-a", "agent-b"}
