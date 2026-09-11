"""
JWT Validator for Agent Service Authentication.

Provides JWKS-based token validation with delegation chain support
(RFC 8693 Token Exchange). Validates tokens with specific audiences
and extracts actor delegation chains from the 'act' claim.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    InvalidAudienceError,
    InvalidTokenError,
    PyJWTError,
)

logger = logging.getLogger(__name__)


@dataclass
class Actor:
    """Represents an actor in the delegation chain."""

    sub: str
    iss: Optional[str] = None
    claims: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Actor":
        """Create Actor from JWT claim dictionary."""
        return cls(
            sub=data.get("sub", ""),
            iss=data.get("iss"),
            claims={k: v for k, v in data.items() if k not in ("sub", "iss")},
        )


@dataclass
class ValidationResult:
    """Result of JWT validation with delegation chain."""

    payload: Dict[str, Any]
    subject: str
    issuer: str
    audience: List[str]
    delegation_chain: List[Actor] = field(default_factory=list)

    @property
    def user_id(self) -> str:
        """Extract user ID from subject claim."""
        return self.subject

    @property
    def email(self) -> Optional[str]:
        """Extract email from payload if present."""
        return self.payload.get("email") or self.payload.get("preferred_username")

    @property
    def is_delegated(self) -> bool:
        """Check if this token represents delegated access."""
        return len(self.delegation_chain) > 0

    @property
    def original_actor(self) -> Optional[Actor]:
        """Get the original actor at the root of delegation chain."""
        return self.delegation_chain[0] if self.delegation_chain else None

    @property
    def immediate_actor(self) -> Optional[Actor]:
        """Get the immediate actor (closest in delegation chain)."""
        return self.delegation_chain[-1] if self.delegation_chain else None


class JWTValidationError(Exception):
    """Base exception for JWT validation errors."""

    pass


class JWTValidator:
    """
    Validates JWTs with JWKS-based signature verification.

    Supports:
    - RS256 signature validation via JWKS endpoint
    - Audience validation
    - Delegation chain extraction (RFC 8693 'act' claim)
    - Token expiration checking
    - Issuer validation

    Usage:
        validator = JWTValidator(
            jwks_url="https://keycloak.example.com/realms/myrealm/protocol/openid-connect/certs",
            expected_audience="my-service"
        )

        try:
            result = validator.validate_token(token)
            print(f"User: {result.user_id}")
            if result.is_delegated:
                print(f"Acting on behalf of: {result.original_actor.sub}")
        except JWTValidationError as e:
            print(f"Invalid token: {e}")
    """

    def __init__(
        self,
        jwks_url: str,
        expected_audience: Optional[str] = None,
        expected_issuer: Optional[str] = None,
        algorithms: Optional[List[str]] = None,
        cache_jwks: bool = True,
        verify_exp: bool = True,
    ):
        """
        Initialize JWT validator.

        Args:
            jwks_url: JWKS endpoint URL for fetching signing keys
            expected_audience: Expected audience claim (aud). If None, audience is not verified
            expected_issuer: Expected issuer claim (iss). If None, issuer is not verified
            algorithms: List of allowed signing algorithms. Defaults to ["RS256"]
            cache_jwks: Whether to cache JWKS keys. Defaults to True
            verify_exp: Whether to verify token expiration. Defaults to True
        """
        self.jwks_url = jwks_url
        self.expected_audience = expected_audience
        self.expected_issuer = expected_issuer
        self.algorithms = algorithms or ["RS256"]
        self.verify_exp = verify_exp

        # Initialize JWKS client with caching
        self._jwks_client = PyJWKClient(
            jwks_url,
            cache_keys=cache_jwks,
            max_cached_keys=16,
        )

        logger.info(
            "JWTValidator initialized",
            extra={
                "jwks_url": jwks_url,
                "audience": expected_audience,
                "issuer": expected_issuer,
                "algorithms": self.algorithms,
            },
        )

    def validate_token(self, token: str) -> ValidationResult:
        """
        Validate a JWT token and extract delegation chain.

        Args:
            token: JWT token string (without "Bearer " prefix)

        Returns:
            ValidationResult containing decoded payload, user info, and delegation chain

        Raises:
            JWTValidationError: If token validation fails
        """
        try:
            # Get signing key from JWKS
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)

            # Configure decode options
            decode_options = {
                "verify_signature": True,
                "verify_exp": self.verify_exp,
                "verify_aud": self.expected_audience is not None,
                "verify_iss": self.expected_issuer is not None,
            }

            # Decode and validate token
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=self.algorithms,
                audience=self.expected_audience,
                issuer=self.expected_issuer,
                options=decode_options,
            )

            # Extract standard claims
            subject = payload.get("sub")
            if not subject:
                raise JWTValidationError("Token missing required 'sub' claim")

            issuer = payload.get("iss", "")
            audience = payload.get("aud", [])
            if isinstance(audience, str):
                audience = [audience]

            # Extract delegation chain from 'act' claim (RFC 8693)
            delegation_chain = self._extract_delegation_chain(payload)

            result = ValidationResult(
                payload=payload,
                subject=subject,
                issuer=issuer,
                audience=audience,
                delegation_chain=delegation_chain,
            )

            logger.debug(
                "Token validated successfully",
                extra={
                    "subject": subject,
                    "audience": audience,
                    "is_delegated": result.is_delegated,
                    "chain_length": len(delegation_chain),
                },
            )

            return result

        except ExpiredSignatureError as e:
            logger.warning("Token expired", extra={"error": str(e)})
            raise JWTValidationError("Token has expired") from e

        except InvalidAudienceError as e:
            logger.warning(
                "Invalid audience",
                extra={"error": str(e), "expected": self.expected_audience},
            )
            raise JWTValidationError(
                f"Invalid audience. Expected: {self.expected_audience}"
            ) from e

        except DecodeError as e:
            logger.warning("Token decode failed", extra={"error": str(e)})
            raise JWTValidationError("Failed to decode token") from e

        except InvalidTokenError as e:
            logger.warning("Invalid token", extra={"error": str(e)})
            raise JWTValidationError(f"Invalid token: {e}") from e

        except PyJWTError as e:
            logger.error("JWT validation error", extra={"error": str(e)})
            raise JWTValidationError(f"Token validation failed: {e}") from e

        except Exception as e:
            logger.error(
                "Unexpected error during token validation", extra={"error": str(e)}
            )
            raise JWTValidationError(f"Unexpected validation error: {e}") from e

    def _extract_delegation_chain(self, payload: Dict[str, Any]) -> List[Actor]:
        """
        Extract delegation chain from 'act' claim (RFC 8693).

        The 'act' claim contains nested actor information for token exchange
        scenarios where a service acts on behalf of a user.

        Example JWT structure with delegation:
        {
            "sub": "service-account-123",
            "act": {
                "sub": "user-456",
                "act": {
                    "sub": "original-service-789"
                }
            }
        }

        Args:
            payload: Decoded JWT payload

        Returns:
            List of Actor objects from root to immediate actor
        """
        chain = []
        current = payload.get("act")

        # Traverse nested 'act' claims to build delegation chain
        while current and isinstance(current, dict):
            actor = Actor.from_dict(current)
            chain.append(actor)
            current = current.get("act")

        return chain

    def validate_header(self, authorization_header: str) -> ValidationResult:
        """
        Validate JWT from Authorization header.

        Args:
            authorization_header: Authorization header value (e.g., "Bearer <token>")

        Returns:
            ValidationResult containing decoded payload, user info, and delegation chain

        Raises:
            JWTValidationError: If header format is invalid or token validation fails
        """
        if not authorization_header:
            raise JWTValidationError("Authorization header is required")

        if not authorization_header.startswith("Bearer "):
            raise JWTValidationError(
                "Invalid Authorization header format. Expected: 'Bearer <token>'"
            )

        token = authorization_header[7:]  # Remove "Bearer " prefix
        return self.validate_token(token)

    def decode_without_verification(self, token: str) -> Dict[str, Any]:
        """
        Decode JWT without signature verification (for inspection only).

        WARNING: This method does not validate the token. Only use for
        debugging or logging purposes where you need to inspect token
        contents without verification.

        Args:
            token: JWT token string

        Returns:
            Decoded payload dictionary

        Raises:
            JWTValidationError: If token cannot be decoded
        """
        try:
            return jwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=self.algorithms,
            )
        except DecodeError as e:
            raise JWTValidationError(f"Failed to decode token: {e}") from e
