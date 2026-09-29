"""Tests for SPIRE volume mount handling in the Helm deployment template."""

import os
import re

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOYMENT_TPL = os.path.join(
    REPO_ROOT, "helm", "templates", "_service-deployment.tpl"
)
ENV_HELPERS_TPL = os.path.join(
    REPO_ROOT, "helm", "templates", "_env-helpers.tpl"
)


def _read_template(path: str) -> str:
    with open(path) as f:
        return f.read()


class TestSpireVolumeMount:
    """Verify the Helm deployment template correctly handles SPIRE volume mounts."""

    @pytest.fixture(autouse=True)
    def load_templates(self):
        self.deployment_tpl = _read_template(DEPLOYMENT_TPL)
        self.env_helpers_tpl = _read_template(ENV_HELPERS_TPL)

    def test_volume_mount_spire_agent_socket_present(self):
        """Template text contains 'spire-agent-socket' volumeMount when spire.enabled."""
        # Look for a volumeMount block with name: spire-agent-socket
        assert "name: spire-agent-socket" in self.deployment_tpl
        # Specifically check it appears in a volumeMounts context
        pattern = r"volumeMounts:.*?name: spire-agent-socket"
        match = re.search(pattern, self.deployment_tpl, re.DOTALL)
        assert match is not None, (
            "Expected 'spire-agent-socket' volumeMount entry under volumeMounts"
        )

    def test_hostpath_volume_for_spire_sockets(self):
        """Template text contains hostPath volume for /run/spire/sockets."""
        # The volumes section should define a hostPath pointing to /run/spire/sockets
        assert "path: /run/spire/sockets" in self.deployment_tpl
        pattern = r"volumes:.*?name: spire-agent-socket.*?hostPath:.*?path: /run/spire/sockets"
        match = re.search(pattern, self.deployment_tpl, re.DOTALL)
        assert match is not None, (
            "Expected hostPath volume with path /run/spire/sockets under volumes"
        )

    def test_volume_mount_is_read_only(self):
        """The spire-agent-socket volumeMount has readOnly: true."""
        # Find the volumeMount block for spire-agent-socket and verify readOnly
        pattern = (
            r"name: spire-agent-socket\s+"
            r"mountPath: /run/spire/sockets\s+"
            r"readOnly: true"
        )
        match = re.search(pattern, self.deployment_tpl)
        assert match is not None, (
            "Expected spire-agent-socket volumeMount to have readOnly: true"
        )

    def test_hostpath_type_directory_or_create(self):
        """The hostPath volume type is DirectoryOrCreate."""
        pattern = (
            r"name: spire-agent-socket\s+"
            r"hostPath:\s+"
            r"path: /run/spire/sockets\s+"
            r"type: DirectoryOrCreate"
        )
        match = re.search(pattern, self.deployment_tpl)
        assert match is not None, (
            "Expected hostPath type to be DirectoryOrCreate"
        )

    def test_spiffe_endpoint_socket_env_var_when_spire_enabled(self):
        """SPIFFE_ENDPOINT_SOCKET env var is set in _env-helpers.tpl when spire.enabled."""
        # The spireEnvVars helper should set SPIFFE_ENDPOINT_SOCKET
        assert "SPIFFE_ENDPOINT_SOCKET" in self.env_helpers_tpl

        # Verify it is inside the spire.enabled conditional block
        pattern = (
            r'\{\{-\s*if\s+\.Values\.spire\.enabled\s*\}\}'
            r'.*?'
            r'name:\s*SPIFFE_ENDPOINT_SOCKET\s*\n'
            r'\s*value:\s*"/run/spire/sockets/agent\.sock"'
        )
        match = re.search(pattern, self.env_helpers_tpl, re.DOTALL)
        assert match is not None, (
            "Expected SPIFFE_ENDPOINT_SOCKET to be set to "
            '"/run/spire/sockets/agent.sock" inside spire.enabled block'
        )

    def test_volume_mount_conditional_on_spire_enabled(self):
        """The spire-agent-socket volumeMount is conditional on spire.enabled."""
        # The volumeMount block for spire-agent-socket should be wrapped in
        # {{- if $context.Values.spire.enabled }}
        pattern = (
            r'\{\{-\s*if\s+\$context\.Values\.spire\.enabled\s*\}\}\s*'
            r'- name: spire-agent-socket\s+'
            r'mountPath: /run/spire/sockets\s+'
            r'readOnly: true\s*'
            r'\{\{-\s*end\s*\}\}'
        )
        match = re.search(pattern, self.deployment_tpl)
        assert match is not None, (
            "Expected spire-agent-socket volumeMount to be wrapped in "
            "spire.enabled conditional"
        )

    def test_volume_definition_conditional_on_spire_enabled(self):
        """The spire-agent-socket volume definition is conditional on spire.enabled."""
        # The volume block for spire-agent-socket should be wrapped in
        # {{- if $context.Values.spire.enabled }}
        pattern = (
            r'\{\{-\s*if\s+\$context\.Values\.spire\.enabled\s*\}\}\s*'
            r'- name: spire-agent-socket\s+'
            r'hostPath:\s+'
            r'path: /run/spire/sockets\s+'
            r'type: DirectoryOrCreate\s*'
            r'\{\{-\s*end\s*\}\}'
        )
        match = re.search(pattern, self.deployment_tpl)
        assert match is not None, (
            "Expected spire-agent-socket volume to be wrapped in "
            "spire.enabled conditional"
        )
