"""Tests for Helm NetworkPolicy template (helm/templates/network-policies.yaml).

These tests parse the raw Helm template text (Go template syntax) and verify
that the declared NetworkPolicy resources enforce least-privilege ingress rules.
"""

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "helm"
    / "templates"
    / "network-policies.yaml"
)


def _read_template() -> str:
    """Return the raw text of the network-policies template."""
    return TEMPLATE_PATH.read_text()


def _split_documents(text: str) -> list[str]:
    """Split a multi-document YAML template on '---' boundaries.

    Returns a list of document strings.  The separator line itself is
    stripped, but comments/template directives inside each document are kept.
    """
    docs = re.split(r"^---\s*$", text, flags=re.MULTILINE)
    return [d.strip() for d in docs if d.strip()]


def _find_doc_by_name_pattern(docs: list[str], pattern: str) -> str | None:
    """Return the first document whose *name:* line matches *pattern*."""
    for doc in docs:
        if re.search(pattern, doc):
            return doc
    return None


def _extract_from_labels(doc: str) -> list[str]:
    """Return all 'app:' values found in 'from:' / podSelector blocks.

    Looks for lines like ``app: {{ $fullName }}-<service>`` inside the
    ``ingress:`` section of a document and returns the service suffixes
    (e.g. ``request-manager``, ``agent-service``).
    """
    # Locate the ingress section
    ingress_match = re.search(r"^\s+ingress:\s*$", doc, re.MULTILINE)
    if ingress_match is None:
        return []
    ingress_section = doc[ingress_match.start() :]
    # Grab all app: references in from: podSelector blocks
    return re.findall(
        r"app:\s*\{\{\s*\$fullName\s*\}\}-(\S+)", ingress_section
    )


def _doc_has_ingress_rules(doc: str) -> bool:
    """Return True if the document contains an ``ingress:`` key with rules."""
    return bool(re.search(r"^\s+ingress:\s*$", doc, re.MULTILINE))


# ---------------------------------------------------------------------------
# Conditional-block helpers
# ---------------------------------------------------------------------------


def _extract_praxis_enabled_block(text: str) -> str:
    """Return the text inside ``{{- if .Values.praxis.enabled }}...{{- else}}``."""
    match = re.search(
        r"\{\{-\s*if\s+\.Values\.praxis\.enabled\s*\}\}(.*?)\{\{-\s*else\s*\}\}",
        text,
        re.DOTALL,
    )
    assert match, "Could not find praxis.enabled conditional block"
    return match.group(1)


def _extract_praxis_disabled_block(text: str) -> str:
    """Return the text inside ``{{- else }}...{{- end }}`` for praxis."""
    match = re.search(
        r"\{\{-\s*else\s*\}\}(.*?)\{\{-\s*end\s*\}\}",
        text,
        re.DOTALL,
    )
    assert match, "Could not find praxis-disabled (else) block"
    return match.group(1)


# ===========================================================================
# Tests
# ===========================================================================


class TestNetworkPolicies:
    """Verify that the Helm NetworkPolicy template enforces least-privilege."""

    @pytest.fixture(autouse=True)
    def _load_template(self) -> None:
        self.raw = _read_template()
        self.docs = _split_documents(self.raw)

    # ---- 1. No broad allow-same-namespace policy ----

    def test_no_broad_allow_same_namespace(self) -> None:
        """There must be no NetworkPolicy with podSelector:{} that also
        contains an ingress rule allowing all pods (i.e. a blanket
        allow-same-namespace policy)."""
        for doc in self.docs:
            # Only care about docs that target all pods (podSelector: {})
            spec_match = re.search(
                r"spec:\s*\n\s+podSelector:\s*\{\}", doc
            )
            if spec_match is None:
                continue
            # This document targets all pods.  It must NOT have ingress rules
            # that would broadly allow traffic.
            if _doc_has_ingress_rules(doc):
                # If there are ingress rules, they must not use an open
                # podSelector (from: - podSelector: {}) which would allow
                # all pods in the namespace.
                ingress_section = doc[
                    re.search(r"ingress:", doc).start() :
                ]
                assert not re.search(
                    r"from:\s*\n\s+-\s*podSelector:\s*\{\}",
                    ingress_section,
                ), (
                    "Found a broad allow-same-namespace policy with "
                    "podSelector:{} and an ingress rule allowing all pods"
                )

    # ---- 2. default-deny-ingress exists ----

    def test_default_deny_ingress_exists(self) -> None:
        """A default-deny-ingress policy must exist with podSelector:{} and
        NO ingress rules (i.e. deny all ingress by default)."""
        doc = _find_doc_by_name_pattern(self.docs, r"default-deny-ingress")
        assert doc is not None, "default-deny-ingress policy not found"

        # Must have podSelector: {}
        assert re.search(
            r"podSelector:\s*\{\}", doc
        ), "default-deny-ingress must use podSelector: {}"

        # Must list Ingress in policyTypes
        assert re.search(
            r"policyTypes:\s*\n\s*-\s*Ingress", doc
        ), "default-deny-ingress must declare policyTypes: [Ingress]"

        # Must NOT have any ingress rules
        assert not _doc_has_ingress_rules(
            doc
        ), "default-deny-ingress must not have ingress rules"

    # ---- 3. agent-service ingress allows only from praxis (praxis.enabled) ----

    def test_agent_service_ingress_praxis_enabled(self) -> None:
        """When praxis.enabled, the agent-service NetworkPolicy must allow
        ingress ONLY from praxis pods."""
        praxis_block = _extract_praxis_enabled_block(self.raw)
        praxis_docs = _split_documents(praxis_block)

        agent_doc = _find_doc_by_name_pattern(
            praxis_docs, r"agent-service"
        )
        assert (
            agent_doc is not None
        ), "agent-service policy not found in praxis-enabled block"

        from_labels = _extract_from_labels(agent_doc)
        assert from_labels == ["praxis"], (
            f"agent-service (praxis.enabled) should allow only from praxis, "
            f"got {from_labels}"
        )

    # ---- 4. praxis ingress allows only from request-manager ----

    def test_praxis_ingress_allows_only_request_manager(self) -> None:
        """When praxis.enabled, the praxis NetworkPolicy must allow ingress
        ONLY from request-manager pods."""
        praxis_block = _extract_praxis_enabled_block(self.raw)
        praxis_docs = _split_documents(praxis_block)

        praxis_doc = _find_doc_by_name_pattern(praxis_docs, r"praxis-ingress")
        assert (
            praxis_doc is not None
        ), "praxis-ingress policy not found in praxis-enabled block"

        from_labels = _extract_from_labels(praxis_doc)
        assert from_labels == ["request-manager"], (
            f"praxis ingress should allow only from request-manager, "
            f"got {from_labels}"
        )

    # ---- 5. request-manager ingress allows only from pf-chat-ui ----

    def test_request_manager_ingress_allows_only_pf_chat_ui(self) -> None:
        """request-manager ingress must allow only from pf-chat-ui pods."""
        doc = _find_doc_by_name_pattern(
            self.docs, r"request-manager-ingress"
        )
        assert doc is not None, "request-manager-ingress policy not found"

        from_labels = _extract_from_labels(doc)
        assert from_labels == ["pf-chat-ui"], (
            f"request-manager ingress should allow only from pf-chat-ui, "
            f"got {from_labels}"
        )

    # ---- 6. postgresql ingress allows only from request-manager and agent-service ----

    def test_postgresql_ingress(self) -> None:
        """postgresql ingress must allow only from request-manager and
        agent-service."""
        doc = _find_doc_by_name_pattern(self.docs, r"postgresql-ingress")
        assert doc is not None, "postgresql-ingress policy not found"

        from_labels = _extract_from_labels(doc)
        assert set(from_labels) == {"request-manager", "agent-service"}, (
            f"postgresql ingress should allow request-manager and "
            f"agent-service, got {from_labels}"
        )
        assert len(from_labels) == 2, (
            f"postgresql ingress should have exactly 2 from entries, "
            f"got {len(from_labels)}"
        )

    # ---- 7. keycloak ingress allows from request-manager, agent-service, kubernetes-agent ----

    def test_keycloak_ingress(self) -> None:
        """keycloak ingress must allow from request-manager, agent-service,
        and kubernetes-agent."""
        doc = _find_doc_by_name_pattern(self.docs, r"keycloak-ingress")
        assert doc is not None, "keycloak-ingress policy not found"

        from_labels = _extract_from_labels(doc)
        expected = {"request-manager", "agent-service", "kubernetes-agent"}
        assert set(from_labels) == expected, (
            f"keycloak ingress should allow {expected}, got {from_labels}"
        )
        assert len(from_labels) == 3, (
            f"keycloak ingress should have exactly 3 from entries, "
            f"got {len(from_labels)}"
        )

    # ---- 8. rag-api ingress allows from agent-service and kubernetes-agent ----

    def test_rag_api_ingress(self) -> None:
        """rag-api ingress must allow from agent-service and
        kubernetes-agent."""
        doc = _find_doc_by_name_pattern(self.docs, r"rag-api-ingress")
        assert doc is not None, "rag-api-ingress policy not found"

        from_labels = _extract_from_labels(doc)
        assert set(from_labels) == {"agent-service", "kubernetes-agent"}, (
            f"rag-api ingress should allow agent-service and "
            f"kubernetes-agent, got {from_labels}"
        )
        assert len(from_labels) == 2, (
            f"rag-api ingress should have exactly 2 from entries, "
            f"got {len(from_labels)}"
        )

    # ---- 9. kubernetes-agent ingress allows only from agent-service ----

    def test_kubernetes_agent_ingress(self) -> None:
        """kubernetes-agent ingress must allow only from agent-service."""
        doc = _find_doc_by_name_pattern(
            self.docs, r"kubernetes-agent-ingress"
        )
        assert doc is not None, "kubernetes-agent-ingress policy not found"

        from_labels = _extract_from_labels(doc)
        assert from_labels == ["agent-service"], (
            f"kubernetes-agent ingress should allow only from agent-service, "
            f"got {from_labels}"
        )

    # ---- 10. agent-service ingress (praxis disabled) allows only request-manager ----

    def test_agent_service_ingress_praxis_disabled(self) -> None:
        """When praxis is disabled (else block), agent-service must allow
        ingress only from request-manager directly."""
        praxis_disabled_block = _extract_praxis_disabled_block(self.raw)
        disabled_docs = _split_documents(praxis_disabled_block)

        agent_doc = _find_doc_by_name_pattern(
            disabled_docs, r"agent-service"
        )
        assert (
            agent_doc is not None
        ), "agent-service policy not found in praxis-disabled block"

        from_labels = _extract_from_labels(agent_doc)
        assert from_labels == ["request-manager"], (
            f"agent-service (praxis disabled) should allow only from "
            f"request-manager, got {from_labels}"
        )
