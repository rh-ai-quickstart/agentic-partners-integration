"""
Token Audit Tracker for SOC 2 Compliance (CC7.1, CC7.2).

Provides token masking, token ID extraction, and comprehensive audit logging
for token exchange operations. Ensures sensitive token values are never logged
in full while maintaining audit trail integrity.

This module supports:
- Safe token masking (first 20 + last 8 chars)
- Token ID extraction (jti claim or hash-based fallback)
- Detailed token exchange audit events
- Delegation chain tracking
"""

import hashlib
from typing import Dict, List, Optional

import jwt
import structlog

from shared_models.audit import AuditService

logger = structlog.get_logger()


class TokenAuditTracker:
    """
    Static utility class for tracking and auditing token operations.

    All methods are static to enable easy integration without instantiation.
    Token values are masked to prevent credential leakage in logs while
    preserving enough information for debugging and correlation.
    """

    @staticmethod
    def mask_token(token: str) -> str:
        """
        Mask a JWT token for safe logging.

        Returns the first 20 characters and last 8 characters of the token,
        with ellipsis in between. This preserves enough information for
        debugging (header + partial signature) while protecting the token.

        Format: eyJhbGciOiJSUzI1NiIs...reBr1oxS

        Args:
            token: Full JWT token string

        Returns:
            Masked token string safe for logging

        Example:
            >>> token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.TJVA95OrM7E2cBab30RMHrHDcEfxjoYZgeFONFh7HgQ"
            >>> masked = TokenAuditTracker.mask_token(token)
            >>> print(masked)
            eyJhbGciOiJSUzI1NiIs...ONFh7HgQ
        """
        if not token:
            return "empty_token"

        if len(token) <= 28:  # Too short to mask meaningfully
            return token[:8] + "..." + token[-4:]

        return token[:20] + "..." + token[-8:]

    @staticmethod
    def extract_token_id(token: str) -> str:
        """
        Extract a unique identifier from a JWT token.

        Attempts to extract the 'jti' (JWT ID) claim from the token.
        If jti is not present, falls back to SHA-256 hash of the token.

        The jti claim is the standard JWT claim for unique token identifiers.
        When not available, a hash provides a stable, reproducible ID that
        doesn't expose the token value but allows correlation across logs.

        Args:
            token: Full JWT token string

        Returns:
            Token identifier (jti claim or SHA-256 hash)

        Example:
            >>> # Token with jti claim
            >>> token_with_jti = "eyJhbGci...with_jti"
            >>> token_id = TokenAuditTracker.extract_token_id(token_with_jti)
            >>> print(token_id)  # Returns the jti claim value

            >>> # Token without jti - uses hash
            >>> token_no_jti = "eyJhbGci...no_jti"
            >>> token_id = TokenAuditTracker.extract_token_id(token_no_jti)
            >>> print(token_id)  # Returns SHA-256 hash
        """
        if not token:
            return "empty_token_id"

        try:
            # Decode token without verification to extract jti claim
            # This is safe for audit logging - we only need to read the claim
            payload = jwt.decode(
                token,
                options={"verify_signature": False}
            )

            # Check for jti (JWT ID) claim first
            jti = payload.get("jti")
            if jti:
                logger.debug(
                    "Extracted jti claim from token",
                    jti=jti,
                    masked_token=TokenAuditTracker.mask_token(token)
                )
                return str(jti)

            # No jti - fall back to hash
            logger.debug(
                "No jti claim found, using hash for token ID",
                masked_token=TokenAuditTracker.mask_token(token)
            )

        except jwt.DecodeError as e:
            # Token is malformed - use hash
            logger.warning(
                "Failed to decode token for jti extraction, using hash",
                error=str(e),
                masked_token=TokenAuditTracker.mask_token(token)
            )
        except Exception as e:
            # Unexpected error - use hash
            logger.warning(
                "Unexpected error extracting jti, using hash",
                error=str(e),
                error_type=type(e).__name__,
                masked_token=TokenAuditTracker.mask_token(token)
            )

        # Hash the token to create a stable ID
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return f"hash_{token_hash[:16]}"  # First 16 chars of hash

    @staticmethod
    async def log_token_exchange(
        original_token: str,
        exchanged_token: str,
        source_agent: str,
        target_agent: str,
        delegation_chain: List[str],
        outcome: str = "success",
        reason: str = "",
        source_ip: str = "",
        additional_metadata: Optional[Dict] = None,
    ) -> None:
        """
        Log a token exchange operation with comprehensive audit trail.

        Emits a detailed audit event showing the transformation from Token 1
        to Token 2, including:
        - Masked token values (safe for logging)
        - Token IDs for correlation
        - Source and target agents
        - Full delegation chain
        - Audience claims before/after
        - Outcome and reason

        This provides complete visibility into token exchanges while
        protecting credential values.

        Args:
            original_token: Original JWT before exchange (Token 1)
            exchanged_token: New JWT after exchange (Token 2)
            source_agent: Agent requesting the exchange
            target_agent: Agent for whom the token is being exchanged
            delegation_chain: Full delegation chain (newest to oldest)
            outcome: Exchange outcome ("success" or "failure")
            reason: Human-readable reason for outcome
            source_ip: Client IP address (optional)
            additional_metadata: Additional context to include in audit event

        Returns:
            None (fire-and-forget audit logging)

        Example:
            >>> await TokenAuditTracker.log_token_exchange(
            ...     original_token="eyJhbGci...original",
            ...     exchanged_token="eyJhbGci...exchanged",
            ...     source_agent="request-manager",
            ...     target_agent="kubernetes-agent",
            ...     delegation_chain=["request-manager", "gateway"],
            ...     outcome="success",
            ...     reason="Token exchanged via RFC 8693",
            ...     source_ip="192.168.1.100"
            ... )
        """
        # Extract token IDs for correlation
        original_token_id = TokenAuditTracker.extract_token_id(original_token)
        exchanged_token_id = TokenAuditTracker.extract_token_id(exchanged_token)

        # Mask tokens for safe logging
        masked_original = TokenAuditTracker.mask_token(original_token)
        masked_exchanged = TokenAuditTracker.mask_token(exchanged_token)

        # Extract audience claims from both tokens
        original_aud = "unknown"
        exchanged_aud = "unknown"
        original_exp = None
        exchanged_exp = None
        original_sub = "unknown"
        exchanged_sub = "unknown"

        try:
            original_payload = jwt.decode(
                original_token,
                options={"verify_signature": False}
            )
            original_aud = original_payload.get("aud", "unknown")
            original_exp = original_payload.get("exp")
            original_sub = original_payload.get("sub", "unknown")
        except Exception as e:
            logger.warning(
                "Failed to decode original token for audit metadata",
                error=str(e),
                masked_token=masked_original
            )

        try:
            exchanged_payload = jwt.decode(
                exchanged_token,
                options={"verify_signature": False}
            )
            exchanged_aud = exchanged_payload.get("aud", "unknown")
            exchanged_exp = exchanged_payload.get("exp")
            exchanged_sub = exchanged_payload.get("sub", "unknown")
        except Exception as e:
            logger.warning(
                "Failed to decode exchanged token for audit metadata",
                error=str(e),
                masked_token=masked_exchanged
            )

        # Build comprehensive metadata
        metadata = {
            # Token transformation
            "token_1_id": original_token_id,
            "token_2_id": exchanged_token_id,
            "token_1_masked": masked_original,
            "token_2_masked": masked_exchanged,
            "transformation": f"{original_token_id} -> {exchanged_token_id}",

            # Audience transformation
            "original_aud": original_aud,
            "exchanged_aud": exchanged_aud,
            "aud_transformation": f"{original_aud} -> {exchanged_aud}",

            # Subject claims
            "original_sub": original_sub,
            "exchanged_sub": exchanged_sub,

            # Expiration times
            "original_exp": original_exp,
            "exchanged_exp": exchanged_exp,

            # Agents
            "source_agent": source_agent,
            "target_agent": target_agent,

            # Delegation chain
            "delegation_chain": delegation_chain,
            "delegation_depth": len(delegation_chain),

            # Token lengths (for debugging)
            "token_1_length": len(original_token),
            "token_2_length": len(exchanged_token),
        }

        # Merge additional metadata if provided
        if additional_metadata:
            metadata.update(additional_metadata)

        # Log structured event first
        logger.info(
            "Token exchange audit event",
            event_type="token.exchange.audit",
            outcome=outcome,
            source_agent=source_agent,
            target_agent=target_agent,
            token_transformation=f"{original_token_id} -> {exchanged_token_id}",
            aud_transformation=f"{original_aud} -> {exchanged_aud}",
            delegation_chain=delegation_chain,
            delegation_depth=len(delegation_chain),
        )

        # Emit audit event to database
        try:
            await AuditService.emit(
                event_type="token.exchange.audit",
                actor=source_agent,
                action="exchange_token",
                resource=target_agent,
                outcome=outcome,
                reason=reason or f"Token exchanged: {original_aud} -> {exchanged_aud}",
                metadata=metadata,
                source_ip=source_ip,
                service="request-manager",
            )

            logger.debug(
                "Token exchange audit event emitted successfully",
                token_1_id=original_token_id,
                token_2_id=exchanged_token_id,
            )

        except Exception as e:
            # Never crash the request due to audit logging failure
            # This matches the pattern in AuditService.emit
            logger.error(
                "Failed to emit token exchange audit event",
                error=str(e),
                error_type=type(e).__name__,
                source_agent=source_agent,
                target_agent=target_agent,
                token_1_id=original_token_id,
                token_2_id=exchanged_token_id,
            )
