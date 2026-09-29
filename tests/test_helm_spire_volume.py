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
        assert "path: /run/spire/sockets" in self.deployment_tpl
        assert "hostPath:" in self.deployment_tpl

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
        """The hostPath volume type is DirectoryOrCreate (in non-CSI mode)."""
        assert "type: DirectoryOrCreate" in self.deployment_tpl
        pattern = (
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
        pattern = (
            r'\{\{-\s*if\s+\$context\.Values\.spire\.enabled\s*\}\}\s*'
            r'- name: spire-agent-socket\s+'
        )
        match = re.search(pattern, self.deployment_tpl)
        assert match is not None, (
            "Expected spire-agent-socket volume to be wrapped in "
            "spire.enabled conditional"
        )

    def test_csi_driver_option_present(self):
        """Template supports SPIFFE CSI driver as alternative to hostPath."""
        assert 'csi.spiffe.io' in self.deployment_tpl
        assert 'spire.csiDriver' in self.deployment_tpl

    def test_pod_labels_include_component(self):
        """Pods have app.kubernetes.io/component label for SPIRE registration matching."""
        assert 'app.kubernetes.io/component' in self.deployment_tpl


class TestClusterSpiffeIDRegistration:
    """Verify ClusterSpiffeID CRDs are generated for each service."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        path = os.path.join(REPO_ROOT, "helm", "templates", "spire-registration.yaml")
        with open(path) as f:
            self.registration_tpl = f.read()

    def test_conditional_on_spire_enabled(self):
        assert "spire.enabled" in self.registration_tpl

    def test_cluster_spiffe_id_kind(self):
        assert "kind: ClusterSPIFFEID" in self.registration_tpl

    def test_spiffe_id_template(self):
        assert "spiffeIDTemplate" in self.registration_tpl
        assert "spire.trustDomain" in self.registration_tpl

    def test_pod_selector_uses_standard_labels(self):
        """Pod selector uses app.kubernetes.io/name (chart name) and app.kubernetes.io/component."""
        assert "app.kubernetes.io/name" in self.registration_tpl
        assert "app.kubernetes.io/component" in self.registration_tpl

    def test_pod_selector_uses_chart_name_not_fullname(self):
        """Pod selector references partner-agent.name (chart name), not fullname."""
        assert 'partner-agent.name' in self.registration_tpl

    def test_service_account_uses_helper(self):
        """SPIFFE ID and workload selector reference the actual SA from serviceAccountName helper."""
        assert 'partner-agent.serviceAccountName' in self.registration_tpl

    def test_namespace_selector(self):
        assert "namespaceSelector" in self.registration_tpl

    def test_jwt_ttl(self):
        assert "jwtTTL" in self.registration_tpl

    def test_services_covered(self):
        for svc in ("request-manager", "agent-service", "kubernetes-agent", "praxis"):
            assert svc in self.registration_tpl, f"Missing ClusterSpiffeID for {svc}"
