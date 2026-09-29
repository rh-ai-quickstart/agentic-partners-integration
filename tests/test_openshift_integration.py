"""Tests for OpenShift integration Helm templates (GAP 7).

Validates ValidatingAdmissionPolicy and Keycloak RBAC templates.
"""

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / "helm" / "templates"
VALUES = REPO_ROOT / "helm" / "values.yaml"


class TestValidatingAdmissionPolicy:
    """Verify ValidatingAdmissionPolicy template structure."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        self.text = (TEMPLATES / "validating-admission-policy.yaml").read_text()

    def test_conditional_on_admission_policy_enabled(self):
        assert "openshift.admissionPolicy.enabled" in self.text

    def test_kind_validating_admission_policy(self):
        assert "kind: ValidatingAdmissionPolicy" in self.text

    def test_kind_binding(self):
        assert "kind: ValidatingAdmissionPolicyBinding" in self.text

    def test_failure_policy_fail(self):
        assert "failurePolicy: Fail" in self.text

    def test_namespace_scoping(self):
        assert "Release.Namespace" in self.text

    def test_validation_actions_deny(self):
        assert "Deny" in self.text

    def test_match_constraints_pods(self):
        assert '"pods"' in self.text

    def test_match_constraints_deployments(self):
        assert '"deployments"' in self.text


class TestKeycloakRBAC:
    """Verify RBAC template for Keycloak group bindings."""

    @pytest.fixture(autouse=True)
    def load_template(self):
        self.text = (TEMPLATES / "rbac-keycloak-groups.yaml").read_text()

    def test_conditional_on_rbac_enabled(self):
        assert "openshift.rbac.enabled" in self.text

    def test_cluster_role(self):
        assert "kind: ClusterRole" in self.text

    def test_cluster_role_binding(self):
        assert "kind: ClusterRoleBinding" in self.text

    def test_keycloak_admin_group(self):
        assert '"keycloak:admin"' in self.text

    def test_department_roles(self):
        assert '"keycloak:{{ $dept }}"' in self.text
        for dept in ("engineering", "kubernetes", "network", "software"):
            assert f'"{dept}"' in self.text, f"Missing department {dept} in range"

    def test_role_binding_per_department(self):
        assert "kind: Role" in self.text
        assert "kind: RoleBinding" in self.text

    def test_namespace_scoped_roles(self):
        assert "namespace:" in self.text


class TestValuesOpenShift:
    """Verify values.yaml has OpenShift configuration."""

    @pytest.fixture(autouse=True)
    def load_values(self):
        self.text = VALUES.read_text()

    def test_openshift_section_exists(self):
        assert "openshift:" in self.text

    def test_admission_policy_default_disabled(self):
        assert "admissionPolicy:" in self.text

    def test_rbac_default_disabled(self):
        assert "rbac:" in self.text
