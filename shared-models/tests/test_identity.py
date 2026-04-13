"""Tests for shared_models.identity module."""

from unittest.mock import MagicMock, patch

from shared_models.identity import (
    WorkloadIdentity,
    extract_identity,
    make_spiffe_id,
    outbound_identity_headers,
)


class TestWorkloadIdentity:
    """Tests for WorkloadIdentity dataclass."""

    def test_entity_type_parses_user(self):
        wid = WorkloadIdentity(
            spiffe_id="spiffe://example.com/user/alice"
        )
        assert wid.entity_type == "user"

    def test_entity_type_parses_service(self):
        wid = WorkloadIdentity(
            spiffe_id="spiffe://example.com/service/request-manager"
        )
        assert wid.entity_type == "service"

    def test_entity_type_parses_agent(self):
        wid = WorkloadIdentity(
            spiffe_id="spiffe://example.com/agent/software-support"
        )
        assert wid.entity_type == "agent"

    def test_entity_type_single_path_segment(self):
        """A SPIFFE ID with a single path segment has the domain as entity_type."""
        wid = WorkloadIdentity(spiffe_id="spiffe://example.com/solo")
        # parts = ['spiffe:', '', 'example.com', 'solo'], parts[-2] = 'example.com'
        assert wid.entity_type == "example.com"

    def test_entity_type_unknown_for_bare_id(self):
        """A bare string with only one segment returns 'unknown'."""
        wid = WorkloadIdentity(spiffe_id="solo")
        assert wid.entity_type == "unknown"

    def test_name_extracts_last_segment(self):
        wid = WorkloadIdentity(
            spiffe_id="spiffe://example.com/user/alice"
        )
        assert wid.name == "alice"

    def test_name_extracts_service_name(self):
        wid = WorkloadIdentity(
            spiffe_id="spiffe://example.com/service/request-manager"
        )
        assert wid.name == "request-manager"

    def test_trailing_slash_handled(self):
        wid = WorkloadIdentity(
            spiffe_id="spiffe://example.com/user/bob/"
        )
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
    """Tests for extract_identity() in mock mode."""

    @patch("shared_models.identity.MOCK_SPIFFE", True)
    def test_extracts_from_header_in_mock_mode(self):
        request = MagicMock()
        request.headers = {"X-SPIFFE-ID": "spiffe://example.com/user/alice"}

        identity = extract_identity(request)
        assert identity is not None
        assert identity.spiffe_id == "spiffe://example.com/user/alice"
        assert identity.name == "alice"

    @patch("shared_models.identity.MOCK_SPIFFE", True)
    def test_returns_none_when_no_header(self):
        request = MagicMock()
        request.headers = {}

        identity = extract_identity(request)
        assert identity is None

    @patch("shared_models.identity.MOCK_SPIFFE", False)
    def test_real_mode_no_transport_returns_none(self):
        request = MagicMock()
        request.scope = {}

        identity = extract_identity(request)
        assert identity is None

    @patch("shared_models.identity.MOCK_SPIFFE", False)
    def test_real_mode_no_peercert_returns_none(self):
        request = MagicMock()
        transport = MagicMock()
        transport.get_extra_info.return_value = None
        request.scope = {"transport": transport}

        identity = extract_identity(request)
        assert identity is None

    @patch("shared_models.identity.MOCK_SPIFFE", False)
    def test_real_mode_extracts_spiffe_from_peercert(self):
        """Real mode should extract SPIFFE ID from mTLS peer certificate SAN."""
        request = MagicMock()
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

    @patch("shared_models.identity.MOCK_SPIFFE", False)
    def test_real_mode_no_spiffe_san_returns_none(self):
        """Real mode with cert but no SPIFFE SAN should return None."""
        request = MagicMock()
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

    @patch("shared_models.identity.MOCK_SPIFFE", False)
    def test_real_mode_empty_san_returns_none(self):
        """Real mode with cert but empty SAN should return None."""
        request = MagicMock()
        transport = MagicMock()
        peercert = {"subjectAltName": ()}
        transport.get_extra_info.return_value = peercert
        request.scope = {"transport": transport}

        identity = extract_identity(request)
        assert identity is None

    @patch("shared_models.identity.MOCK_SPIFFE", False)
    def test_real_mode_no_san_key_returns_none(self):
        """Real mode with cert but no subjectAltName key should return None."""
        request = MagicMock()
        transport = MagicMock()
        peercert = {}
        transport.get_extra_info.return_value = peercert
        request.scope = {"transport": transport}

        identity = extract_identity(request)
        assert identity is None


class TestOutboundIdentityHeaders:
    """Tests for outbound_identity_headers()."""

    @patch("shared_models.identity.MOCK_SPIFFE", True)
    @patch("shared_models.identity.TRUST_DOMAIN", "test.example.com")
    def test_mock_mode_sets_spiffe_header(self):
        headers = outbound_identity_headers("request-manager")
        assert headers["X-SPIFFE-ID"] == "spiffe://test.example.com/service/request-manager"

    @patch("shared_models.identity.MOCK_SPIFFE", False)
    def test_real_mode_no_spiffe_header(self):
        headers = outbound_identity_headers("request-manager")
        assert "X-SPIFFE-ID" not in headers

    @patch("shared_models.identity.MOCK_SPIFFE", True)
    @patch("shared_models.identity.TRUST_DOMAIN", "test.example.com")
    def test_delegation_user_header(self):
        headers = outbound_identity_headers(
            "request-manager",
            delegation_user="spiffe://test.example.com/user/alice",
        )
        assert headers["X-Delegation-User"] == "spiffe://test.example.com/user/alice"

    @patch("shared_models.identity.MOCK_SPIFFE", True)
    @patch("shared_models.identity.TRUST_DOMAIN", "test.example.com")
    def test_delegation_agent_header(self):
        headers = outbound_identity_headers(
            "request-manager",
            delegation_agent="spiffe://test.example.com/agent/support",
        )
        assert headers["X-Delegation-Agent"] == "spiffe://test.example.com/agent/support"

    @patch("shared_models.identity.MOCK_SPIFFE", True)
    @patch("shared_models.identity.TRUST_DOMAIN", "test.example.com")
    def test_both_delegation_headers(self):
        headers = outbound_identity_headers(
            "request-manager",
            delegation_user="spiffe://test.example.com/user/alice",
            delegation_agent="spiffe://test.example.com/agent/support",
        )
        assert "X-SPIFFE-ID" in headers
        assert "X-Delegation-User" in headers
        assert "X-Delegation-Agent" in headers

    @patch("shared_models.identity.MOCK_SPIFFE", False)
    def test_no_delegation_returns_empty(self):
        headers = outbound_identity_headers("request-manager")
        assert headers == {}
