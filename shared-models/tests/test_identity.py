"""Tests for shared_models.identity module."""

from unittest.mock import MagicMock, patch

import pytest

from shared_models.identity import (
    WorkloadIdentity,
    extract_identity,
    make_spiffe_id,
    outbound_identity_headers,
)


class TestWorkloadIdentity:
    """Tests for WorkloadIdentity dataclass."""

    def test_entity_type_parses_user(self):
        wid = WorkloadIdentity(spiffe_id="spiffe://example.com/user/alice")
        assert wid.entity_type == "user"

    def test_entity_type_parses_service(self):
        wid = WorkloadIdentity(spiffe_id="spiffe://example.com/service/request-manager")
        assert wid.entity_type == "service"

    def test_entity_type_parses_agent(self):
        wid = WorkloadIdentity(spiffe_id="spiffe://example.com/agent/software-support")
        assert wid.entity_type == "agent"

    def test_entity_type_single_path_segment(self):
        """A SPIFFE ID with a single path segment has the domain as entity_type."""
        wid = WorkloadIdentity(spiffe_id="spiffe://example.com/solo")
        assert wid.entity_type == "example.com"

    def test_entity_type_unknown_for_bare_id(self):
        """A bare string with only one segment returns 'unknown'."""
        wid = WorkloadIdentity(spiffe_id="solo")
        assert wid.entity_type == "unknown"

    def test_name_extracts_last_segment(self):
        wid = WorkloadIdentity(spiffe_id="spiffe://example.com/user/alice")
        assert wid.name == "alice"

    def test_name_extracts_service_name(self):
        wid = WorkloadIdentity(spiffe_id="spiffe://example.com/service/request-manager")
        assert wid.name == "request-manager"

    def test_trailing_slash_handled(self):
        wid = WorkloadIdentity(spiffe_id="spiffe://example.com/user/bob/")
        assert wid.entity_type == "user"
        assert wid.name == "bob"


class TestMakeSpiffeId:
    """Tests for make_spiffe_id()."""

    @patch("shared_models.identity.TRUST_DOMAIN", "test.example.com")
    def test_builds_user_id(self):
        result = make_spiffe_id("user", "alice")
        assert result == "spiffe://test.example.com/user/alice"

    @patch("shared_models.identity.TRUST_DOMAIN", "test.example.com")
    def test_builds_service_id(self):
        result = make_spiffe_id("service", "request-manager")
        assert result == "spiffe://test.example.com/service/request-manager"

    @patch("shared_models.identity.TRUST_DOMAIN", "test.example.com")
    def test_builds_agent_id(self):
        result = make_spiffe_id("agent", "software-support")
        assert result == "spiffe://test.example.com/agent/software-support"


class TestExtractIdentity:
    """Tests for extract_identity()."""

    @patch("shared_models.identity.SPIFFE_MODE", "mock")
    def test_extracts_from_header_in_mock_mode(self):
        """X-SPIFFE-ID header is trusted in mock mode."""
        request = MagicMock()
        request.headers = {"X-SPIFFE-ID": "spiffe://example.com/user/alice"}
        request.scope = {}

        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == "spiffe://example.com/user/alice"
        assert identity.name == "alice"

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    def test_extracts_from_header_in_spire_header_mode(self):
        """X-SPIFFE-ID header is trusted in spire-header mode."""
        request = MagicMock()
        request.headers = {"X-SPIFFE-ID": "spiffe://example.com/user/alice"}
        request.scope = {}

        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == "spiffe://example.com/user/alice"
        assert identity.name == "alice"

    @patch("shared_models.identity.SPIFFE_MODE", "mtls")
    def test_ignores_header_in_mtls_mode(self):
        """X-SPIFFE-ID header is ignored in mtls mode."""
        request = MagicMock()
        request.headers = {"X-SPIFFE-ID": "spiffe://example.com/user/alice"}
        request.scope = {}

        identity = extract_identity(request)
        assert identity is None

    def test_returns_none_when_no_header_no_transport(self):
        request = MagicMock()
        request.headers = {}
        request.scope = {}

        identity = extract_identity(request)
        assert identity is None

    def test_no_peercert_returns_none(self):
        request = MagicMock()
        request.headers = {}
        transport = MagicMock()
        transport.get_extra_info.return_value = None
        request.scope = {"transport": transport}

        identity = extract_identity(request)
        assert identity is None

    def test_extracts_spiffe_from_peercert(self):
        """Should extract SPIFFE ID from mTLS peer certificate SAN."""
        request = MagicMock()
        request.headers = {}
        transport = MagicMock()
        peercert = {
            "subjectAltName": [
                ("DNS", "example.com"),
                ("URI", "spiffe://trust.domain/service/my-svc"),
            ]
        }
        transport.get_extra_info.return_value = peercert
        request.scope = {"transport": transport}

        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == "spiffe://trust.domain/service/my-svc"
        assert identity.name == "my-svc"
        assert identity.entity_type == "service"

    def test_no_spiffe_san_returns_none(self):
        """Cert with non-SPIFFE SAN should return None."""
        request = MagicMock()
        request.headers = {}
        transport = MagicMock()
        peercert = {
            "subjectAltName": [
                ("DNS", "example.com"),
                ("URI", "https://example.com/not-spiffe"),
            ]
        }
        transport.get_extra_info.return_value = peercert
        request.scope = {"transport": transport}

        identity = extract_identity(request)
        assert identity is None

    def test_empty_san_returns_none(self):
        """Cert with empty SAN should return None."""
        request = MagicMock()
        request.headers = {}
        transport = MagicMock()
        peercert = {"subjectAltName": ()}
        transport.get_extra_info.return_value = peercert
        request.scope = {"transport": transport}

        identity = extract_identity(request)
        assert identity is None

    def test_no_san_key_returns_none(self):
        """Cert without subjectAltName key should return None."""
        request = MagicMock()
        request.headers = {}
        transport = MagicMock()
        peercert = {}
        transport.get_extra_info.return_value = peercert
        request.scope = {"transport": transport}

        identity = extract_identity(request)
        assert identity is None

    def test_mtls_cert_takes_precedence_over_header(self):
        """mTLS peer cert takes precedence over X-SPIFFE-ID header."""
        request = MagicMock()
        request.headers = {"X-SPIFFE-ID": "spiffe://example.com/user/alice"}
        transport = MagicMock()
        peercert = {
            "subjectAltName": [
                ("URI", "spiffe://trust.domain/service/my-svc"),
            ]
        }
        transport.get_extra_info.return_value = peercert
        request.scope = {"transport": transport}

        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == "spiffe://trust.domain/service/my-svc"


class TestOutboundIdentityHeaders:
    """Tests for outbound_identity_headers()."""

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    @patch("shared_models.identity.get_spire_client")
    def test_sets_spiffe_header_from_spire(self, mock_get_spire):
        mock_client = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test.example.com/service/request-manager"
        mock_client.fetch_svid.return_value = mock_svid
        mock_get_spire.return_value = mock_client

        headers = outbound_identity_headers("request-manager")
        assert headers["X-SPIFFE-ID"] == "spiffe://test.example.com/service/request-manager"

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    @patch("shared_models.identity.get_spire_client")
    def test_raises_when_svid_fetch_fails(self, mock_get_spire):
        mock_client = MagicMock()
        mock_client.fetch_svid.return_value = None
        mock_get_spire.return_value = mock_client

        with pytest.raises(RuntimeError, match="Failed to fetch SVID"):
            outbound_identity_headers("request-manager")

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    @patch("shared_models.identity.get_spire_client")
    def test_delegation_user_header(self, mock_get_spire):
        mock_client = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test.example.com/service/request-manager"
        mock_client.fetch_svid.return_value = mock_svid
        mock_get_spire.return_value = mock_client

        headers = outbound_identity_headers(
            "request-manager",
            delegation_user="spiffe://test.example.com/user/alice",
        )
        assert headers["X-Delegation-User"] == "spiffe://test.example.com/user/alice"

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    @patch("shared_models.identity.get_spire_client")
    def test_delegation_agent_header(self, mock_get_spire):
        mock_client = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test.example.com/service/request-manager"
        mock_client.fetch_svid.return_value = mock_svid
        mock_get_spire.return_value = mock_client

        headers = outbound_identity_headers(
            "request-manager",
            delegation_agent="spiffe://test.example.com/agent/support",
        )
        assert headers["X-Delegation-Agent"] == "spiffe://test.example.com/agent/support"

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    @patch("shared_models.identity.get_spire_client")
    def test_both_delegation_headers(self, mock_get_spire):
        mock_client = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test.example.com/service/request-manager"
        mock_client.fetch_svid.return_value = mock_svid
        mock_get_spire.return_value = mock_client

        headers = outbound_identity_headers(
            "request-manager",
            delegation_user="spiffe://test.example.com/user/alice",
            delegation_agent="spiffe://test.example.com/agent/support",
        )
        assert "X-SPIFFE-ID" in headers
        assert "X-Delegation-User" in headers
        assert "X-Delegation-Agent" in headers

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    @patch("shared_models.identity.get_spire_client")
    def test_request_id_header_when_provided(self, mock_get_spire):
        mock_client = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test.example.com/service/request-manager"
        mock_client.fetch_svid.return_value = mock_svid
        mock_get_spire.return_value = mock_client

        headers = outbound_identity_headers(
            "request-manager",
            request_id="req-abc-123",
        )
        assert headers["X-Request-ID"] == "req-abc-123"

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    @patch("shared_models.identity.get_spire_client")
    def test_no_request_id_header_when_omitted(self, mock_get_spire):
        mock_client = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test.example.com/service/request-manager"
        mock_client.fetch_svid.return_value = mock_svid
        mock_get_spire.return_value = mock_client

        headers = outbound_identity_headers("request-manager")
        assert "X-Request-ID" not in headers

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    @patch("shared_models.identity.get_spire_client")
    def test_no_request_id_header_when_none(self, mock_get_spire):
        mock_client = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test.example.com/service/request-manager"
        mock_client.fetch_svid.return_value = mock_svid
        mock_get_spire.return_value = mock_client

        headers = outbound_identity_headers("request-manager", request_id=None)
        assert "X-Request-ID" not in headers

    @patch("shared_models.identity.SPIFFE_MODE", "mtls")
    @patch("shared_models.identity.get_spire_client")
    def test_mtls_mode_fetches_real_svid(self, mock_get_spire):
        mock_client = MagicMock()
        mock_svid = MagicMock()
        mock_svid.spiffe_id = "spiffe://test.example.com/service/request-manager"
        mock_client.fetch_svid.return_value = mock_svid
        mock_get_spire.return_value = mock_client

        headers = outbound_identity_headers("request-manager")
        assert headers["X-SPIFFE-ID"] == "spiffe://test.example.com/service/request-manager"

    @patch("shared_models.identity.SPIFFE_MODE", "mock")
    def test_mock_mode_generates_synthetic_identity(self):
        headers = outbound_identity_headers("request-manager")
        assert headers["X-SPIFFE-ID"] == "spiffe://partner.example.com/service/request-manager"

    @patch("shared_models.identity.SPIFFE_MODE", "mock")
    def test_mock_mode_with_delegation(self):
        headers = outbound_identity_headers(
            "request-manager",
            delegation_user="spiffe://partner.example.com/user/alice",
            delegation_agent="spiffe://partner.example.com/agent/k8s",
            request_id="req-123",
        )
        assert headers["X-SPIFFE-ID"] == "spiffe://partner.example.com/service/request-manager"
        assert headers["X-Delegation-User"] == "spiffe://partner.example.com/user/alice"
        assert headers["X-Delegation-Agent"] == "spiffe://partner.example.com/agent/k8s"
        assert headers["X-Request-ID"] == "req-123"


class TestIdentityTrustModel:
    """Tests for the X-SPIFFE-ID header trust model in extract_identity().

    Validates per-mode behavior:
    - ``mtls``: ignores X-SPIFFE-ID header; only trusts mTLS peer cert.
    - ``mock``: trusts the X-SPIFFE-ID header (dev convenience).
    - ``spire-header``: trusts the X-SPIFFE-ID header (real SVIDs, NetworkPolicy-guarded).
    - mTLS peer certificate always takes precedence regardless of mode.
    """

    def _make_request(self, *, header_spiffe_id=None, peercert=None):
        request = MagicMock(spec=["headers", "scope"])
        request.headers = {}
        if header_spiffe_id:
            request.headers["X-SPIFFE-ID"] = header_spiffe_id

        if peercert is not None:
            transport = MagicMock()
            transport.get_extra_info.return_value = peercert
            request.scope = {"transport": transport}
        else:
            request.scope = {}

        return request

    def _make_peercert(self, spiffe_id):
        return {
            "subjectAltName": [
                ("URI", spiffe_id),
            ]
        }

    @patch("shared_models.identity.SPIFFE_MODE", "mtls")
    def test_mtls_mode_ignores_header(self):
        request = self._make_request(
            header_spiffe_id="spiffe://example.com/user/attacker",
        )
        identity = extract_identity(request)
        assert identity is None

    @patch("shared_models.identity.SPIFFE_MODE", "mtls")
    def test_mtls_mode_uses_cert(self):
        cert_spiffe = "spiffe://trust.domain/service/legit-svc"
        request = self._make_request(
            peercert=self._make_peercert(cert_spiffe),
        )
        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == cert_spiffe
        assert identity.name == "legit-svc"
        assert identity.entity_type == "service"

    @patch("shared_models.identity.SPIFFE_MODE", "mtls")
    def test_mtls_mode_cert_takes_precedence(self):
        cert_spiffe = "spiffe://trust.domain/service/real-svc"
        header_spiffe = "spiffe://evil.domain/user/attacker"
        request = self._make_request(
            header_spiffe_id=header_spiffe,
            peercert=self._make_peercert(cert_spiffe),
        )
        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == cert_spiffe
        assert identity.spiffe_id != header_spiffe

    @patch("shared_models.identity.SPIFFE_MODE", "mock")
    def test_mock_mode_trusts_header(self):
        header_spiffe = "spiffe://dev.local/user/developer"
        request = self._make_request(
            header_spiffe_id=header_spiffe,
        )
        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == header_spiffe
        assert identity.name == "developer"

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    def test_spire_header_mode_trusts_header(self):
        header_spiffe = "spiffe://partner.example.com/service/agent-service"
        request = self._make_request(
            header_spiffe_id=header_spiffe,
        )
        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == header_spiffe

    @patch("shared_models.identity.SPIFFE_MODE", "mock")
    def test_mock_mode_mtls_takes_precedence(self):
        cert_spiffe = "spiffe://trust.domain/service/real-svc"
        header_spiffe = "spiffe://dev.local/user/developer"
        request = self._make_request(
            header_spiffe_id=header_spiffe,
            peercert=self._make_peercert(cert_spiffe),
        )
        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == cert_spiffe
        assert identity.spiffe_id != header_spiffe

    @patch("shared_models.identity.SPIFFE_MODE", "spire-header")
    def test_spire_header_mode_mtls_takes_precedence(self):
        cert_spiffe = "spiffe://trust.domain/service/real-svc"
        header_spiffe = "spiffe://partner.example.com/service/agent-service"
        request = self._make_request(
            header_spiffe_id=header_spiffe,
            peercert=self._make_peercert(cert_spiffe),
        )
        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == cert_spiffe

    @patch("shared_models.identity.SPIFFE_MODE", "mtls")
    def test_no_identity_when_nothing_provided(self):
        request = self._make_request()
        identity = extract_identity(request)
        assert identity is None
