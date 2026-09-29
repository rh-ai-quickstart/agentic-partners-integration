"""Tests for agent_service.jwt_auth — per-hop JWT validation module.

Covers:
- TokenClaims dataclass properties (is_delegated, actor_subject)
- JWTAuthError
- validate_bearer_token: all branches
- _get_jwks_client singleton
"""

from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest


class TestTokenClaims:
    """Tests for the TokenClaims dataclass."""

    def test_is_delegated_with_act(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="user-1",
            issuer="iss",
            audience=["agent-service"],
            act={"sub": "service-account-abc"},
        )
        assert claims.is_delegated is True

    def test_is_delegated_without_act(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="user-1",
            issuer="iss",
            audience=["agent-service"],
        )
        assert claims.is_delegated is False

    def test_actor_subject_from_act(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="user-1",
            issuer="iss",
            audience=["agent-service"],
            act={"sub": "dcr-client-xyz"},
            azp="partner-agent-ui",
        )
        assert claims.actor_subject == "dcr-client-xyz"

    def test_actor_subject_fallback_to_azp(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="user-1",
            issuer="iss",
            audience=["agent-service"],
            azp="partner-agent-ui",
        )
        assert claims.actor_subject == "partner-agent-ui"

    def test_actor_subject_none_when_no_act_no_azp(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="user-1",
            issuer="iss",
            audience=["agent-service"],
        )
        assert claims.actor_subject is None

    def test_actor_subject_act_is_dict_without_sub(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="user-1",
            issuer="iss",
            audience=["agent-service"],
            act={"iss": "some-issuer"},
            azp="fallback-azp",
        )
        # act is dict but has no "sub" key → returns None from act.get("sub"),
        # so falls through to... actually no, `self.act.get("sub")` returns None
        # and that's returned directly.
        assert claims.actor_subject is None


class TestCallerIdentity:
    """Tests for TokenClaims.caller_identity fallback chain."""

    def test_caller_identity_from_sub(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="user-123",
            issuer="iss",
            audience=["agent-service"],
            raw={"sub": "user-123", "azp": "partner-agent-ui"},
        )
        assert claims.caller_identity == "user-123"

    def test_caller_identity_fallback_to_client_id(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="",
            issuer="iss",
            audience=["agent-service"],
            raw={"client_id": "request-manager-svc", "azp": "rm"},
        )
        assert claims.caller_identity == "request-manager-svc"

    def test_caller_identity_fallback_to_azp(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="",
            issuer="iss",
            audience=["agent-service"],
            raw={"azp": "partner-agent-ui"},
        )
        assert claims.caller_identity == "partner-agent-ui"

    def test_caller_identity_fallback_to_preferred_username(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="",
            issuer="iss",
            audience=["agent-service"],
            raw={"preferred_username": "carlos@example.com"},
        )
        assert claims.caller_identity == "carlos@example.com"

    def test_caller_identity_ultimate_fallback_to_subject_field(self):
        from agent_service.jwt_auth import TokenClaims

        claims = TokenClaims(
            subject="fallback-sub",
            issuer="iss",
            audience=["agent-service"],
            raw={},
        )
        assert claims.caller_identity == "fallback-sub"


class TestJWTAuthError:
    def test_default_message(self):
        from agent_service.jwt_auth import JWTAuthError

        err = JWTAuthError()
        assert str(err) == "Authentication failed"

    def test_custom_message(self):
        from agent_service.jwt_auth import JWTAuthError

        err = JWTAuthError("Custom error")
        assert str(err) == "Custom error"


class TestValidateBearerToken:
    """Tests for validate_bearer_token function."""

    def test_disabled_raises_error(self):
        from agent_service.jwt_auth import JWTAuthError, validate_bearer_token

        with patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", False):
            with pytest.raises(JWTAuthError, match="JWT validation is disabled"):
                validate_bearer_token("Bearer some-token")

    def test_missing_authorization_header(self):
        from agent_service.jwt_auth import JWTAuthError, validate_bearer_token

        with patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True):
            with pytest.raises(JWTAuthError, match="Authentication failed"):
                validate_bearer_token(None)

    def test_non_bearer_authorization_header(self):
        from agent_service.jwt_auth import JWTAuthError, validate_bearer_token

        with patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True):
            with pytest.raises(JWTAuthError, match="Authentication failed"):
                validate_bearer_token("Basic dXNlcjpwYXNz")

    def test_empty_string_authorization_header(self):
        from agent_service.jwt_auth import JWTAuthError, validate_bearer_token

        with patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True):
            with pytest.raises(JWTAuthError, match="Authentication failed"):
                validate_bearer_token("")

    def test_expired_token(self):
        from agent_service.jwt_auth import JWTAuthError, validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", side_effect=pyjwt.ExpiredSignatureError("expired")),
        ):
            with pytest.raises(JWTAuthError, match="Authentication failed"):
                validate_bearer_token("Bearer some-expired-token")

    def test_invalid_audience(self):
        from agent_service.jwt_auth import JWTAuthError, validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", side_effect=pyjwt.InvalidAudienceError("bad aud")),
        ):
            with pytest.raises(JWTAuthError, match="Authentication failed"):
                validate_bearer_token("Bearer some-token")

    def test_invalid_signature_pyjwt_error(self):
        from agent_service.jwt_auth import JWTAuthError, validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", side_effect=pyjwt.PyJWTError("bad sig")),
        ):
            with pytest.raises(JWTAuthError, match="Authentication failed"):
                validate_bearer_token("Bearer some-token")

    def test_missing_sub_claim(self):
        from agent_service.jwt_auth import JWTAuthError, validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value={
                "iss": "http://keycloak:8080/realms/partner-agent",
                "aud": "agent-service",
                # no "sub" key
            }),
        ):
            with pytest.raises(JWTAuthError, match="Authentication failed"):
                validate_bearer_token("Bearer some-token")

    def test_success_full_claims(self):
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-123",
            "iss": "http://keycloak:8080/realms/partner-agent",
            "aud": "agent-service",
            "email": "user@example.com",
            "groups": ["/engineering", "/software"],
            "act": {"sub": "dcr-client-abc"},
            "azp": "partner-agent-ui",
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload),
        ):
            result = validate_bearer_token("Bearer valid-token")

        assert result.subject == "user-123"
        assert result.issuer == "http://keycloak:8080/realms/partner-agent"
        assert result.audience == ["agent-service"]
        assert result.email == "user@example.com"
        assert result.groups == ["engineering", "software"]
        assert result.act == {"sub": "dcr-client-abc"}
        assert result.azp == "partner-agent-ui"
        assert result.is_delegated is True
        assert result.actor_subject == "dcr-client-abc"
        assert result.raw == payload

    def test_success_audience_as_list(self):
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-1",
            "iss": "iss",
            "aud": ["agent-service", "other-service"],
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload),
        ):
            result = validate_bearer_token("Bearer valid-token")

        assert result.audience == ["agent-service", "other-service"]

    def test_groups_as_string_normalized(self):
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-1",
            "iss": "iss",
            "aud": "agent-service",
            "groups": "/engineering",
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload),
        ):
            result = validate_bearer_token("Bearer valid-token")

        assert result.groups == ["engineering"]

    def test_email_fallback_to_preferred_username(self):
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-1",
            "iss": "iss",
            "aud": "agent-service",
            "preferred_username": "admin",
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload),
        ):
            result = validate_bearer_token("Bearer valid-token")

        assert result.email == "admin"

    def test_custom_expected_audience(self):
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-1",
            "iss": "iss",
            "aud": "custom-audience",
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload) as mock_decode,
        ):
            validate_bearer_token("Bearer valid-token", expected_audience="custom-audience")

        # Verify jwt.decode was called with the custom audience
        mock_decode.assert_called_once()
        call_kwargs = mock_decode.call_args
        assert call_kwargs[1]["audience"] == "custom-audience" or call_kwargs.kwargs.get("audience") == "custom-audience"

    def test_no_aud_in_payload_defaults_to_empty_list(self):
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-1",
            "iss": "iss",
            # no "aud" key
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload),
        ):
            result = validate_bearer_token("Bearer valid-token")

        assert result.audience == []

    def test_groups_with_empty_entries_filtered(self):
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-1",
            "iss": "iss",
            "aud": "agent-service",
            "groups": ["/engineering", "", "/admin"],
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload),
        ):
            result = validate_bearer_token("Bearer valid-token")

        assert result.groups == ["engineering", "admin"]

    def test_no_iss_defaults_to_empty_string(self):
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-1",
            "aud": "agent-service",
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload),
        ):
            result = validate_bearer_token("Bearer valid-token")

        assert result.issuer == ""


class TestGetJwksClient:
    """Tests for _get_jwks_client singleton."""

    def test_creates_client_on_first_call(self):
        import agent_service.jwt_auth as module

        # Reset singleton
        module._jwks_client = None

        with patch("agent_service.jwt_auth.PyJWKClient") as mock_cls:
            mock_cls.return_value = MagicMock()
            client = module._get_jwks_client()
            mock_cls.assert_called_once()
            assert client is mock_cls.return_value

        # Reset for other tests
        module._jwks_client = None

    def test_returns_cached_client_on_second_call(self):
        import agent_service.jwt_auth as module

        # Reset singleton
        module._jwks_client = None

        with patch("agent_service.jwt_auth.PyJWKClient") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            first = module._get_jwks_client()
            second = module._get_jwks_client()
            # Should only create once
            mock_cls.assert_called_once()
            assert first is second

        # Reset for other tests
        module._jwks_client = None


class TestJWTValidationInMainInvoke:
    """Tests for JWT validation wiring in main.py invoke_agent."""

    @patch("agent_service.agents.AgentManager")
    def test_jwt_validation_failure_returns_401(self, mock_agent_manager_cls, monkeypatch):
        """When JWT validation is enabled and token is invalid, returns 401."""
        monkeypatch.setattr("shared_models.identity.SPIFFE_MODE", "mock")
        mock_manager = MagicMock()
        mock_manager.get_specialist_agents.return_value = {}
        mock_agent_manager_cls.return_value = mock_manager

        with (
            patch("agent_service.a2a.server.get_a2a_app", return_value=MagicMock()),
        ):
            import importlib

            import agent_service.main
            importlib.reload(agent_service.main)
            app = agent_service.main.app

        from fastapi.testclient import TestClient

        from agent_service.jwt_auth import JWTAuthError

        with patch("agent_service.main._JWT_VALIDATION_ENABLED", True), \
             patch("agent_service.main.validate_bearer_token", side_effect=JWTAuthError("Authentication failed")):
            client = TestClient(app)
            response = client.post(
                "/api/v1/agents/routing-agent/invoke",
                json={
                    "session_id": "sess-jwt-fail",
                    "user_id": "user@test.com",
                    "message": "Hello",
                },
                headers={
                    "X-SPIFFE-ID": "spiffe://partner.example.com/service/request-manager",
                    "Authorization": "Bearer invalid-token",
                },
            )

        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication failed"

    @patch("agent_service.agents.AgentManager")
    def test_jwt_validation_skipped_when_no_auth_header(self, mock_agent_manager_cls, monkeypatch):
        """When no Authorization header is present, JWT validation is skipped."""
        monkeypatch.setattr("shared_models.identity.SPIFFE_MODE", "mock")
        from unittest.mock import AsyncMock

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = ("Hello!", False)
        mock_manager = MagicMock()
        mock_manager.get_specialist_agents.return_value = {}
        mock_manager.get_agent.return_value = mock_agent
        mock_manager.get_agent_dept_map.return_value = {}
        mock_manager.get_agent_descriptions.return_value = {}
        mock_manager.agents_dict = {"routing-agent": mock_agent}
        mock_agent_manager_cls.return_value = mock_manager

        with patch("agent_service.a2a.server.get_a2a_app", return_value=MagicMock()):
            import importlib

            import agent_service.main
            importlib.reload(agent_service.main)
            app = agent_service.main.app

        from fastapi.testclient import TestClient

        with patch("agent_service.main._JWT_VALIDATION_ENABLED", True), \
             patch("agent_service.main.validate_bearer_token") as mock_validate:
            client = TestClient(app)
            response = client.post(
                "/api/v1/agents/routing-agent/invoke",
                json={
                    "session_id": "sess-no-auth",
                    "user_id": "user@test.com",
                    "message": "Hello",
                },
                headers={
                    "X-SPIFFE-ID": "spiffe://partner.example.com/service/request-manager",
                    # No Authorization header
                },
            )

        assert response.status_code == 200
        mock_validate.assert_not_called()

    @patch("agent_service.agents.AgentManager")
    def test_jwt_validation_skipped_when_disabled(self, mock_agent_manager_cls, monkeypatch):
        """When JWT_VALIDATION_ENABLED is False, validation is completely skipped."""
        monkeypatch.setattr("shared_models.identity.SPIFFE_MODE", "mock")
        from unittest.mock import AsyncMock

        mock_agent = AsyncMock()
        mock_agent.create_response_with_retry.return_value = ("Hello!", False)
        mock_manager = MagicMock()
        mock_manager.get_specialist_agents.return_value = {}
        mock_manager.get_agent.return_value = mock_agent
        mock_manager.get_agent_dept_map.return_value = {}
        mock_manager.get_agent_descriptions.return_value = {}
        mock_manager.agents_dict = {"routing-agent": mock_agent}
        mock_agent_manager_cls.return_value = mock_manager

        with patch("agent_service.a2a.server.get_a2a_app", return_value=MagicMock()):
            import importlib

            import agent_service.main
            importlib.reload(agent_service.main)
            app = agent_service.main.app

        from fastapi.testclient import TestClient

        with patch("agent_service.main._JWT_VALIDATION_ENABLED", False), \
             patch("agent_service.main.validate_bearer_token") as mock_validate:
            client = TestClient(app)
            response = client.post(
                "/api/v1/agents/routing-agent/invoke",
                json={
                    "session_id": "sess-disabled",
                    "user_id": "user@test.com",
                    "message": "Hello",
                },
                headers={
                    "X-SPIFFE-ID": "spiffe://partner.example.com/service/request-manager",
                    "Authorization": "Bearer some-token",
                },
            )

        assert response.status_code == 200
        mock_validate.assert_not_called()


class TestIssuerValidation:
    """Tests for JWT issuer validation (issuer= param and InvalidIssuerError handler)."""

    def test_wrong_issuer_raises_jwt_auth_error(self):
        """A token with an issuer from a different realm should raise JWTAuthError."""
        from agent_service.jwt_auth import JWTAuthError, validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch(
                "agent_service.jwt_auth.jwt.decode",
                side_effect=pyjwt.InvalidIssuerError("Invalid issuer"),
            ),
        ):
            with pytest.raises(JWTAuthError, match="Authentication failed"):
                validate_bearer_token("Bearer token-with-wrong-issuer")

    def test_correct_issuer_passes(self):
        """A token whose issuer matches KEYCLOAK_URL/realms/KEYCLOAK_REALM should pass."""
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-issuer-ok",
            "iss": "http://keycloak:8080/realms/partner-agent",
            "aud": "agent-service",
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth.KEYCLOAK_URL", "http://keycloak:8080"),
            patch("agent_service.jwt_auth.KEYCLOAK_REALM", "partner-agent"),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload),
        ):
            result = validate_bearer_token("Bearer valid-issuer-token")

        assert result.subject == "user-issuer-ok"
        assert result.issuer == "http://keycloak:8080/realms/partner-agent"

    def test_expected_issuer_format(self):
        """The expected issuer string must equal '{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}'."""
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        custom_url = "https://sso.example.com"
        custom_realm = "my-realm"

        payload = {
            "sub": "user-1",
            "iss": f"{custom_url}/realms/{custom_realm}",
            "aud": "agent-service",
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth.KEYCLOAK_URL", custom_url),
            patch("agent_service.jwt_auth.KEYCLOAK_REALM", custom_realm),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload) as mock_decode,
        ):
            validate_bearer_token("Bearer some-token")

        # Verify that jwt.decode was called with issuer= matching the expected format
        mock_decode.assert_called_once()
        call_kwargs = mock_decode.call_args
        expected_issuer = f"{custom_url}/realms/{custom_realm}"
        assert call_kwargs.kwargs.get("issuer") == expected_issuer or \
            (len(call_kwargs) > 1 and call_kwargs[1].get("issuer") == expected_issuer)

    def test_verify_iss_option_enabled(self):
        """The options dict passed to jwt.decode must include verify_iss: True."""
        from agent_service.jwt_auth import validate_bearer_token

        mock_jwks_client = MagicMock()
        mock_signing_key = MagicMock()
        mock_jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        payload = {
            "sub": "user-1",
            "iss": "http://keycloak:8080/realms/partner-agent",
            "aud": "agent-service",
        }

        with (
            patch("agent_service.jwt_auth.JWT_VALIDATION_ENABLED", True),
            patch("agent_service.jwt_auth._get_jwks_client", return_value=mock_jwks_client),
            patch("agent_service.jwt_auth.jwt.decode", return_value=payload) as mock_decode,
        ):
            validate_bearer_token("Bearer some-token")

        mock_decode.assert_called_once()
        call_kwargs = mock_decode.call_args
        options = call_kwargs.kwargs.get("options") or call_kwargs[1].get("options", {})
        assert options.get("verify_iss") is True
