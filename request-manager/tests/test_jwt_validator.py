"""Tests for request_manager.jwt_validator — 100% coverage."""

from unittest.mock import MagicMock, patch

import pytest
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidTokenError,
    PyJWTError,
)

from request_manager.jwt_validator import (
    Actor,
    JWTValidationError,
    JWTValidator,
    ValidationResult,
)

# ---------------------------------------------------------------------------
# Actor dataclass
# ---------------------------------------------------------------------------


class TestActor:
    """Tests for the Actor dataclass."""

    def test_from_dict_basic(self):
        """Create Actor from dict with sub and iss."""
        data = {"sub": "user-123", "iss": "https://keycloak.example.com"}
        actor = Actor.from_dict(data)
        assert actor.sub == "user-123"
        assert actor.iss == "https://keycloak.example.com"
        assert actor.claims == {}

    def test_from_dict_extra_claims(self):
        """Extra claims beyond sub and iss are stored in claims dict."""
        data = {"sub": "svc-1", "iss": "issuer", "scope": "read", "custom": "val"}
        actor = Actor.from_dict(data)
        assert actor.sub == "svc-1"
        assert actor.iss == "issuer"
        assert actor.claims == {"scope": "read", "custom": "val"}

    def test_from_dict_missing_sub(self):
        """Missing sub defaults to empty string."""
        actor = Actor.from_dict({"iss": "issuer"})
        assert actor.sub == ""
        assert actor.iss == "issuer"

    def test_from_dict_missing_iss(self):
        """Missing iss defaults to None."""
        actor = Actor.from_dict({"sub": "user"})
        assert actor.iss is None

    def test_from_dict_empty(self):
        """Empty dict gives default values."""
        actor = Actor.from_dict({})
        assert actor.sub == ""
        assert actor.iss is None
        assert actor.claims == {}


# ---------------------------------------------------------------------------
# ValidationResult dataclass
# ---------------------------------------------------------------------------


class TestValidationResult:
    """Tests for ValidationResult properties."""

    def _make_result(self, payload=None, delegation_chain=None):
        return ValidationResult(
            payload=payload or {},
            subject="user-42",
            issuer="https://issuer.example.com",
            audience=["my-service"],
            delegation_chain=delegation_chain or [],
        )

    def test_user_id(self):
        """user_id returns the subject."""
        result = self._make_result()
        assert result.user_id == "user-42"

    def test_email_from_email_claim(self):
        """email returns the email claim when present."""
        result = self._make_result(payload={"email": "alice@example.com"})
        assert result.email == "alice@example.com"

    def test_email_from_preferred_username(self):
        """email falls back to preferred_username when email is absent."""
        result = self._make_result(payload={"preferred_username": "alice"})
        assert result.email == "alice"

    def test_email_none_when_missing(self):
        """email is None when neither email nor preferred_username present."""
        result = self._make_result(payload={})
        assert result.email is None

    def test_is_delegated_true(self):
        """is_delegated is True when delegation_chain is non-empty."""
        actor = Actor(sub="svc-1")
        result = self._make_result(delegation_chain=[actor])
        assert result.is_delegated is True

    def test_is_delegated_false(self):
        """is_delegated is False when delegation_chain is empty."""
        result = self._make_result(delegation_chain=[])
        assert result.is_delegated is False

    def test_original_actor(self):
        """original_actor returns first element of chain."""
        a1 = Actor(sub="root")
        a2 = Actor(sub="immediate")
        result = self._make_result(delegation_chain=[a1, a2])
        assert result.original_actor is a1

    def test_original_actor_none(self):
        """original_actor is None when chain is empty."""
        result = self._make_result(delegation_chain=[])
        assert result.original_actor is None

    def test_immediate_actor(self):
        """immediate_actor returns last element of chain."""
        a1 = Actor(sub="root")
        a2 = Actor(sub="immediate")
        result = self._make_result(delegation_chain=[a1, a2])
        assert result.immediate_actor is a2

    def test_immediate_actor_none(self):
        """immediate_actor is None when chain is empty."""
        result = self._make_result(delegation_chain=[])
        assert result.immediate_actor is None

    # -- azp property ---------------------------------------------------------

    def test_azp_present(self):
        """azp returns the azp claim when present."""
        result = self._make_result(payload={"azp": "partner-agent-ui"})
        assert result.azp == "partner-agent-ui"

    def test_azp_absent(self):
        """azp returns None when no azp claim."""
        result = self._make_result(payload={})
        assert result.azp is None

    # -- actor_identifier property --------------------------------------------

    def test_actor_identifier_from_delegation_chain(self):
        """actor_identifier returns immediate_actor.sub when delegation chain exists."""
        actor = Actor(sub="service-account-rm")
        result = self._make_result(
            payload={"azp": "should-not-use"},
            delegation_chain=[actor],
        )
        assert result.actor_identifier == "service-account-rm"

    def test_actor_identifier_fallback_to_azp(self):
        """actor_identifier falls back to azp when no delegation chain."""
        result = self._make_result(
            payload={"azp": "partner-agent-ui"},
            delegation_chain=[],
        )
        assert result.actor_identifier == "partner-agent-ui"

    def test_actor_identifier_none_when_neither(self):
        """actor_identifier is None when no chain and no azp."""
        result = self._make_result(payload={}, delegation_chain=[])
        assert result.actor_identifier is None


# ---------------------------------------------------------------------------
# JWTValidationError
# ---------------------------------------------------------------------------


class TestJWTValidationError:
    """Tests for JWTValidationError exception."""

    def test_is_exception(self):
        err = JWTValidationError("bad token")
        assert isinstance(err, Exception)
        assert str(err) == "bad token"


# ---------------------------------------------------------------------------
# JWTValidator.__init__
# ---------------------------------------------------------------------------


class TestJWTValidatorInit:
    """Tests for JWTValidator constructor."""

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_init_defaults(self, mock_pyjwk_cls):
        """Constructor sets defaults and creates JWKS client."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        assert v.jwks_url == "https://jwks.example.com/certs"
        assert v.expected_audience is None
        assert v.expected_issuer is None
        assert v.algorithms == ["RS256"]
        assert v.verify_exp is True
        mock_pyjwk_cls.assert_called_once_with(
            "https://jwks.example.com/certs",
            cache_keys=True,
            max_cached_keys=16,
        )

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_init_custom_params(self, mock_pyjwk_cls):
        """Constructor respects custom parameters."""
        v = JWTValidator(
            jwks_url="https://jwks.example.com/certs",
            expected_audience="my-aud",
            expected_issuer="my-iss",
            algorithms=["ES256"],
            cache_jwks=False,
            verify_exp=False,
        )
        assert v.expected_audience == "my-aud"
        assert v.expected_issuer == "my-iss"
        assert v.algorithms == ["ES256"]
        assert v.verify_exp is False
        mock_pyjwk_cls.assert_called_once_with(
            "https://jwks.example.com/certs",
            cache_keys=False,
            max_cached_keys=16,
        )


# ---------------------------------------------------------------------------
# JWTValidator.validate_token — success path
# ---------------------------------------------------------------------------


class TestValidateToken:
    """Tests for JWTValidator.validate_token."""

    def _make_validator(self, mock_pyjwk_cls, aud=None, iss=None):
        mock_pyjwk_cls.return_value = MagicMock()
        return JWTValidator(
            jwks_url="https://jwks.example.com/certs",
            expected_audience=aud,
            expected_issuer=iss,
        )

    @patch("request_manager.jwt_validator.jwt")
    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_success_basic(self, mock_pyjwk_cls, mock_jwt):
        """Successful validation returns ValidationResult with correct fields."""
        v = self._make_validator(mock_pyjwk_cls, aud="my-svc")
        mock_signing_key = MagicMock()
        v._jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        mock_jwt.decode.return_value = {
            "sub": "user-1",
            "iss": "https://issuer",
            "aud": "my-svc",
            "email": "user@example.com",
        }

        result = v.validate_token("test-token")

        assert result.subject == "user-1"
        assert result.issuer == "https://issuer"
        assert result.audience == ["my-svc"]
        assert result.user_id == "user-1"
        assert result.email == "user@example.com"
        assert result.is_delegated is False

    @patch("request_manager.jwt_validator.jwt")
    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_audience_as_list(self, mock_pyjwk_cls, mock_jwt):
        """When aud claim is already a list, use it directly."""
        v = self._make_validator(mock_pyjwk_cls)
        mock_signing_key = MagicMock()
        v._jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        mock_jwt.decode.return_value = {
            "sub": "user-1",
            "iss": "https://issuer",
            "aud": ["aud-a", "aud-b"],
        }

        result = v.validate_token("test-token")
        assert result.audience == ["aud-a", "aud-b"]

    @patch("request_manager.jwt_validator.jwt")
    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_with_delegation_chain(self, mock_pyjwk_cls, mock_jwt):
        """Token with act claim extracts delegation chain."""
        v = self._make_validator(mock_pyjwk_cls)
        mock_signing_key = MagicMock()
        v._jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        mock_jwt.decode.return_value = {
            "sub": "svc-1",
            "iss": "issuer",
            "aud": "target",
            "act": {
                "sub": "gateway",
                "iss": "gw-issuer",
                "act": {"sub": "user-original"},
            },
        }

        result = v.validate_token("test-token")

        assert result.is_delegated is True
        assert len(result.delegation_chain) == 2
        assert result.original_actor.sub == "gateway"
        assert result.immediate_actor.sub == "user-original"

    @patch("request_manager.jwt_validator.jwt")
    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_missing_sub_raises(self, mock_pyjwk_cls, mock_jwt):
        """Missing sub claim raises JWTValidationError."""
        v = self._make_validator(mock_pyjwk_cls)
        mock_signing_key = MagicMock()
        v._jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key

        mock_jwt.decode.return_value = {
            "iss": "issuer",
            "aud": "aud",
        }

        with pytest.raises(JWTValidationError, match="missing required 'sub' claim"):
            v.validate_token("no-sub-token")

    # -- Error branches -------------------------------------------------------

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_expired(self, mock_pyjwk_cls):
        """ExpiredSignatureError raises JWTValidationError."""
        v = self._make_validator(mock_pyjwk_cls)
        v._jwks_client.get_signing_key_from_jwt.side_effect = ExpiredSignatureError()

        with pytest.raises(JWTValidationError, match="Token has expired"):
            v.validate_token("expired-token")

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_invalid_audience(self, mock_pyjwk_cls):
        """InvalidAudienceError raises JWTValidationError."""
        v = self._make_validator(mock_pyjwk_cls, aud="expected-aud")
        v._jwks_client.get_signing_key_from_jwt.side_effect = InvalidAudienceError(
            "wrong aud"
        )

        with pytest.raises(JWTValidationError, match="Invalid audience"):
            v.validate_token("bad-aud-token")

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_decode_error(self, mock_pyjwk_cls):
        """DecodeError raises JWTValidationError."""
        v = self._make_validator(mock_pyjwk_cls)
        v._jwks_client.get_signing_key_from_jwt.side_effect = DecodeError(
            "malformed"
        )

        with pytest.raises(JWTValidationError, match="Failed to decode token"):
            v.validate_token("malformed-token")

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_invalid_token_error(self, mock_pyjwk_cls):
        """InvalidTokenError raises JWTValidationError."""
        v = self._make_validator(mock_pyjwk_cls)
        v._jwks_client.get_signing_key_from_jwt.side_effect = InvalidTokenError(
            "invalid"
        )

        with pytest.raises(JWTValidationError, match="Invalid token"):
            v.validate_token("invalid-token")

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_pyjwt_error(self, mock_pyjwk_cls):
        """PyJWTError raises JWTValidationError."""
        v = self._make_validator(mock_pyjwk_cls)
        v._jwks_client.get_signing_key_from_jwt.side_effect = PyJWTError("generic jwt")

        with pytest.raises(JWTValidationError, match="Token validation failed"):
            v.validate_token("jwt-error-token")

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_token_unexpected_exception(self, mock_pyjwk_cls):
        """Unexpected exceptions are caught and wrapped."""
        v = self._make_validator(mock_pyjwk_cls)
        v._jwks_client.get_signing_key_from_jwt.side_effect = RuntimeError("boom")

        with pytest.raises(JWTValidationError, match="Unexpected validation error"):
            v.validate_token("boom-token")


# ---------------------------------------------------------------------------
# JWTValidator._extract_delegation_chain
# ---------------------------------------------------------------------------


class TestExtractDelegationChain:
    """Tests for _extract_delegation_chain."""

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_no_act_claim(self, mock_pyjwk_cls):
        """No act claim returns empty chain."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        chain = v._extract_delegation_chain({"sub": "user"})
        assert chain == []

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_single_act(self, mock_pyjwk_cls):
        """Single act claim returns single Actor."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        chain = v._extract_delegation_chain({"sub": "svc", "act": {"sub": "user"}})
        assert len(chain) == 1
        assert chain[0].sub == "user"

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_nested_act(self, mock_pyjwk_cls):
        """Nested act claims build correct chain."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        payload = {
            "sub": "svc-c",
            "act": {
                "sub": "svc-b",
                "act": {"sub": "svc-a", "act": {"sub": "user"}},
            },
        }
        chain = v._extract_delegation_chain(payload)
        assert len(chain) == 3
        assert [a.sub for a in chain] == ["svc-b", "svc-a", "user"]

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_act_not_dict(self, mock_pyjwk_cls):
        """Non-dict act claim is ignored."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        chain = v._extract_delegation_chain({"sub": "svc", "act": "invalid"})
        assert chain == []


# ---------------------------------------------------------------------------
# JWTValidator.validate_header
# ---------------------------------------------------------------------------


class TestValidateHeader:
    """Tests for validate_header."""

    @patch("request_manager.jwt_validator.jwt")
    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_header_success(self, mock_pyjwk_cls, mock_jwt):
        """Valid Authorization header is parsed and validated."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        mock_signing_key = MagicMock()
        v._jwks_client.get_signing_key_from_jwt.return_value = mock_signing_key
        mock_jwt.decode.return_value = {
            "sub": "user-1",
            "iss": "issuer",
            "aud": "svc",
        }

        result = v.validate_header("Bearer test-token")
        assert result.subject == "user-1"

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_header_empty(self, mock_pyjwk_cls):
        """Empty header raises JWTValidationError."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        with pytest.raises(JWTValidationError, match="Authorization header is required"):
            v.validate_header("")

    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_validate_header_no_bearer(self, mock_pyjwk_cls):
        """Header without Bearer prefix raises JWTValidationError."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        with pytest.raises(JWTValidationError, match="Invalid Authorization header format"):
            v.validate_header("Basic abc123")


# ---------------------------------------------------------------------------
# JWTValidator.decode_without_verification
# ---------------------------------------------------------------------------


class TestDecodeWithoutVerification:
    """Tests for decode_without_verification."""

    @patch("request_manager.jwt_validator.jwt")
    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_decode_success(self, mock_pyjwk_cls, mock_jwt):
        """Decodes token without signature verification."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        mock_jwt.decode.return_value = {"sub": "user-1", "aud": "svc"}

        result = v.decode_without_verification("test-token")
        assert result == {"sub": "user-1", "aud": "svc"}
        mock_jwt.decode.assert_called_once_with(
            "test-token",
            options={"verify_signature": False},
            algorithms=["RS256"],
        )

    @patch("request_manager.jwt_validator.jwt")
    @patch("request_manager.jwt_validator.PyJWKClient")
    def test_decode_error(self, mock_pyjwk_cls, mock_jwt):
        """DecodeError during unverified decode raises JWTValidationError."""
        v = JWTValidator(jwks_url="https://jwks.example.com/certs")
        mock_jwt.decode.side_effect = DecodeError("bad")

        with pytest.raises(JWTValidationError, match="Failed to decode token"):
            v.decode_without_verification("bad-token")
