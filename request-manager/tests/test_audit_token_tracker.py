"""Tests for audit_token_tracker.py token masking and audit logging."""

import hashlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from request_manager.audit_token_tracker import TokenAuditTracker


class TestTokenMasking:
    """Tests for token masking functionality."""

    def test_mask_token_returns_first20_last8(self):
        """Mask token returns first 20 chars and last 8 chars with ellipsis."""
        # Create a realistic JWT token (header.payload.signature)
        token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiYWRtaW4iOnRydWV9.TJVA95OrM7E2cBab30RMHrHDcEfxjoYZgeFONFh7HgQ"

        masked = TokenAuditTracker.mask_token(token)

        # Verify format: first 20 chars + "..." + last 8 chars
        assert masked.startswith(token[:20])
        assert masked.endswith(token[-8:])
        assert "..." in masked
        assert len(masked) == 20 + 3 + 8  # 31 characters total

        # Verify the actual masked format
        expected = f"{token[:20]}...{token[-8:]}"
        assert masked == expected

    def test_mask_token_handles_short_tokens(self):
        """Mask token handles tokens shorter than 28 characters."""
        short_token = "short_token_abc123"  # 18 chars

        masked = TokenAuditTracker.mask_token(short_token)

        # Should use shorter format: first 8 + "..." + last 4
        assert masked == f"{short_token[:8]}...{short_token[-4:]}"
        assert "..." in masked

    def test_mask_token_handles_empty_token(self):
        """Mask token handles empty or None token."""
        assert TokenAuditTracker.mask_token("") == "empty_token"
        assert TokenAuditTracker.mask_token(None) == "empty_token"

    def test_mask_token_preserves_debugging_info(self):
        """Masked token preserves JWT header and partial signature for debugging."""
        # JWT tokens always start with "eyJ" (base64 encoded '{"')
        jwt_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload_section_here.signature_section_here_abcd1234"

        masked = TokenAuditTracker.mask_token(jwt_token)

        # Verify JWT header is visible
        assert masked.startswith("eyJhbGciOiJSUzI1NiIs")
        # Verify partial signature is visible
        assert masked.endswith("cd1234")


class TestTokenIdExtraction:
    """Tests for token ID extraction functionality."""

    @patch("request_manager.audit_token_tracker.jwt")
    def test_extract_token_id_gets_jti_claim(self, mock_jwt):
        """Extract token ID successfully retrieves jti claim from JWT."""
        # Mock JWT decode to return payload with jti
        mock_jwt.decode.return_value = {
            "sub": "user123",
            "aud": "kubernetes-agent",
            "jti": "unique-token-id-12345"
        }

        token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiJ1bmlxdWUtdG9rZW4taWQtMTIzNDUifQ.signature"
        token_id = TokenAuditTracker.extract_token_id(token)

        # Should return the jti claim value
        assert token_id == "unique-token-id-12345"

    @patch("request_manager.audit_token_tracker.jwt")
    def test_extract_token_id_falls_back_to_hash_without_jti(self, mock_jwt):
        """Extract token ID falls back to hash when jti claim is missing."""
        # Mock JWT decode to return payload without jti
        mock_jwt.decode.return_value = {
            "sub": "user123",
            "aud": "kubernetes-agent"
        }

        token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMTIzIn0.signature"
        token_id = TokenAuditTracker.extract_token_id(token)

        # Should return hash-based ID
        assert token_id.startswith("hash_")

        # Verify it's a consistent hash
        expected_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
        assert token_id == f"hash_{expected_hash}"

    def test_extract_token_id_handles_malformed_token(self):
        """Extract token ID handles malformed JWT gracefully."""
        malformed_token = "not.a.valid.jwt.token"

        token_id = TokenAuditTracker.extract_token_id(malformed_token)

        # Should fall back to hash
        assert token_id.startswith("hash_")

    def test_extract_token_id_handles_empty_token(self):
        """Extract token ID handles empty or None token."""
        assert TokenAuditTracker.extract_token_id("") == "empty_token_id"
        assert TokenAuditTracker.extract_token_id(None) == "empty_token_id"

    @patch("request_manager.audit_token_tracker.jwt")
    def test_extract_token_id_consistency(self, mock_jwt):
        """Extract token ID returns consistent hash for same token."""
        # Mock JWT decode to return payload without jti (forcing hash)
        mock_jwt.decode.return_value = {"sub": "user123"}

        token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyMTIzIn0.signature"

        # Extract ID multiple times
        id1 = TokenAuditTracker.extract_token_id(token)
        id2 = TokenAuditTracker.extract_token_id(token)
        id3 = TokenAuditTracker.extract_token_id(token)

        # All should be identical
        assert id1 == id2 == id3


@pytest.mark.asyncio
class TestTokenExchangeAudit:
    """Tests for token exchange audit logging."""

    @patch("request_manager.audit_token_tracker.jwt")
    @patch("request_manager.audit_token_tracker.AuditService")
    async def test_log_token_exchange_emits_audit_event(self, mock_audit, mock_jwt):
        """Log token exchange successfully emits audit event."""
        # Mock JWT decode to return different payloads for each token
        def decode_side_effect(token, **kwargs):
            if "original" in token:
                return {
                    "sub": "user@example.com",
                    "aud": "request-manager",
                    "jti": "original-token-123"
                }
            else:
                return {
                    "sub": "user@example.com",
                    "aud": "kubernetes-agent",
                    "jti": "exchanged-token-456"
                }

        mock_jwt.decode.side_effect = decode_side_effect

        # Use realistic JWT token strings
        original_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.original.signature123"
        exchanged_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.exchanged.signature456"

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Log token exchange
        await TokenAuditTracker.log_token_exchange(
            original_token=original_token,
            exchanged_token=exchanged_token,
            source_agent="request-manager",
            target_agent="kubernetes-agent",
            delegation_chain=["request-manager", "gateway"],
            outcome="success",
            reason="Token exchanged via RFC 8693",
            source_ip="192.168.1.100"
        )

        # Verify audit event was emitted
        mock_audit.emit.assert_called_once()
        call_args = mock_audit.emit.call_args[1]

        # Verify basic audit fields
        assert call_args["event_type"] == "token.exchange.audit"
        assert call_args["actor"] == "request-manager"
        assert call_args["action"] == "exchange_token"
        assert call_args["resource"] == "kubernetes-agent"
        assert call_args["outcome"] == "success"
        assert call_args["reason"] == "Token exchanged via RFC 8693"
        assert call_args["source_ip"] == "192.168.1.100"
        assert call_args["service"] == "request-manager"

    @patch("request_manager.audit_token_tracker.jwt")
    @patch("request_manager.audit_token_tracker.AuditService")
    async def test_audit_includes_delegation_chain(self, mock_audit, mock_jwt):
        """Audit event includes complete delegation chain in metadata."""
        # Mock JWT decode
        def decode_side_effect(token, **kwargs):
            if "original" in token:
                return {"sub": "user", "aud": "app1"}
            else:
                return {"sub": "user", "aud": "app2"}

        mock_jwt.decode.side_effect = decode_side_effect

        # Create test tokens
        original_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.original.sig1"
        exchanged_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.exchanged.sig2"

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Define delegation chain
        delegation_chain = ["request-manager", "gateway", "load-balancer"]

        # Log token exchange
        await TokenAuditTracker.log_token_exchange(
            original_token=original_token,
            exchanged_token=exchanged_token,
            source_agent="request-manager",
            target_agent="kubernetes-agent",
            delegation_chain=delegation_chain,
        )

        # Verify delegation chain in metadata
        call_args = mock_audit.emit.call_args[1]
        metadata = call_args["metadata"]

        assert metadata["delegation_chain"] == delegation_chain
        assert metadata["delegation_depth"] == 3
        assert "request-manager" in metadata["delegation_chain"]
        assert "gateway" in metadata["delegation_chain"]
        assert "load-balancer" in metadata["delegation_chain"]

    @patch("request_manager.audit_token_tracker.jwt")
    @patch("request_manager.audit_token_tracker.AuditService")
    async def test_audit_includes_both_tokens(self, mock_audit, mock_jwt):
        """Audit event includes both original and exchanged token information."""
        # Mock JWT decode to return different payloads
        def decode_side_effect(token, **kwargs):
            if "original" in token:
                return {
                    "sub": "alice@example.com",
                    "aud": "service-a",
                    "jti": "token-original-001",
                    "exp": 1234567890
                }
            else:
                return {
                    "sub": "alice@example.com",
                    "aud": "service-b",
                    "jti": "token-exchanged-002",
                    "exp": 1234567999
                }

        mock_jwt.decode.side_effect = decode_side_effect

        # Create test tokens with distinct properties
        original_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.original.signature_original"
        exchanged_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.exchanged.signature_exchanged"

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Log token exchange
        await TokenAuditTracker.log_token_exchange(
            original_token=original_token,
            exchanged_token=exchanged_token,
            source_agent="service-a",
            target_agent="service-b",
            delegation_chain=["service-a"],
        )

        # Verify both tokens are in metadata
        call_args = mock_audit.emit.call_args[1]
        metadata = call_args["metadata"]

        # Token IDs
        assert metadata["token_1_id"] == "token-original-001"
        assert metadata["token_2_id"] == "token-exchanged-002"
        assert metadata["transformation"] == "token-original-001 -> token-exchanged-002"

        # Audience claims
        assert metadata["original_aud"] == "service-a"
        assert metadata["exchanged_aud"] == "service-b"
        assert metadata["aud_transformation"] == "service-a -> service-b"

        # Subject claims
        assert metadata["original_sub"] == "alice@example.com"
        assert metadata["exchanged_sub"] == "alice@example.com"

        # Expiration times
        assert metadata["original_exp"] == 1234567890
        assert metadata["exchanged_exp"] == 1234567999

        # Token lengths
        assert metadata["token_1_length"] == len(original_token)
        assert metadata["token_2_length"] == len(exchanged_token)

    @patch("request_manager.audit_token_tracker.jwt")
    @patch("request_manager.audit_token_tracker.AuditService")
    async def test_token_masking_in_audit(self, mock_audit, mock_jwt):
        """Audit event masks sensitive token values while preserving IDs."""
        # Mock JWT decode
        mock_jwt.decode.return_value = {
            "sub": "user@example.com",
            "aud": "kubernetes-agent",
            "jti": "secret-token-id-12345"
        }

        # Create test token (realistic long JWT)
        full_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1c2VyQGV4YW1wbGUuY29tIiwiYXVkIjoia3ViZXJuZXRlcy1hZ2VudCIsImp0aSI6InNlY3JldC10b2tlbi1pZC0xMjM0NSJ9.secret-signature-data-here"

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Log token exchange
        await TokenAuditTracker.log_token_exchange(
            original_token=full_token,
            exchanged_token=full_token,
            source_agent="service-a",
            target_agent="service-b",
            delegation_chain=["service-a"],
        )

        # Verify tokens are masked in metadata
        call_args = mock_audit.emit.call_args[1]
        metadata = call_args["metadata"]

        # Masked tokens should NOT contain the full token
        assert metadata["token_1_masked"] != full_token
        assert metadata["token_2_masked"] != full_token

        # Masked tokens should contain ellipsis
        assert "..." in metadata["token_1_masked"]
        assert "..." in metadata["token_2_masked"]

        # Masked tokens should be much shorter than original
        assert len(metadata["token_1_masked"]) < len(full_token)
        assert len(metadata["token_2_masked"]) < len(full_token)

        # But token IDs should be preserved (extracted from jti claim)
        assert metadata["token_1_id"] == "secret-token-id-12345"
        assert metadata["token_2_id"] == "secret-token-id-12345"

    @patch("request_manager.audit_token_tracker.jwt")
    @patch("request_manager.audit_token_tracker.AuditService")
    async def test_audit_includes_additional_metadata(self, mock_audit, mock_jwt):
        """Audit event merges additional metadata into event."""
        # Mock JWT decode
        def decode_side_effect(token, **kwargs):
            if "original" in token:
                return {"sub": "user", "aud": "app1"}
            else:
                return {"sub": "user", "aud": "app2"}

        mock_jwt.decode.side_effect = decode_side_effect

        # Create test tokens
        original_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.original.sig"
        exchanged_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.exchanged.sig"

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Additional metadata to include
        additional_metadata = {
            "request_id": "req-12345",
            "session_id": "sess-67890",
            "client_version": "1.2.3"
        }

        # Log token exchange with additional metadata
        await TokenAuditTracker.log_token_exchange(
            original_token=original_token,
            exchanged_token=exchanged_token,
            source_agent="service-a",
            target_agent="service-b",
            delegation_chain=["service-a"],
            additional_metadata=additional_metadata
        )

        # Verify additional metadata is included
        call_args = mock_audit.emit.call_args[1]
        metadata = call_args["metadata"]

        assert metadata["request_id"] == "req-12345"
        assert metadata["session_id"] == "sess-67890"
        assert metadata["client_version"] == "1.2.3"

    @patch("request_manager.audit_token_tracker.jwt")
    @patch("request_manager.audit_token_tracker.AuditService")
    async def test_audit_failure_does_not_crash(self, mock_audit, mock_jwt):
        """Failed audit emission does not crash the token exchange flow."""
        # Mock JWT decode
        def decode_side_effect(token, **kwargs):
            if "original" in token:
                return {"sub": "user", "aud": "app1"}
            else:
                return {"sub": "user", "aud": "app2"}

        mock_jwt.decode.side_effect = decode_side_effect

        # Create test tokens
        original_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.original.sig"
        exchanged_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.exchanged.sig"

        # Mock audit service to raise exception
        mock_audit.emit = AsyncMock(side_effect=Exception("Database connection lost"))

        # Should not raise exception - fire and forget
        await TokenAuditTracker.log_token_exchange(
            original_token=original_token,
            exchanged_token=exchanged_token,
            source_agent="service-a",
            target_agent="service-b",
            delegation_chain=["service-a"],
        )

        # Verify audit was attempted
        mock_audit.emit.assert_called_once()

    @patch("request_manager.audit_token_tracker.jwt")
    @patch("request_manager.audit_token_tracker.AuditService")
    async def test_audit_handles_malformed_tokens_gracefully(self, mock_audit, mock_jwt):
        """Audit logging handles malformed tokens without crashing."""
        # Mock JWT decode to raise DecodeError
        mock_jwt.decode.side_effect = mock_jwt.DecodeError("Invalid token")
        mock_jwt.DecodeError = Exception  # For the except clause

        # Use malformed tokens
        malformed_token = "not.a.valid.jwt"

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Should not raise exception
        await TokenAuditTracker.log_token_exchange(
            original_token=malformed_token,
            exchanged_token=malformed_token,
            source_agent="service-a",
            target_agent="service-b",
            delegation_chain=["service-a"],
        )

        # Verify audit was emitted with fallback values
        call_args = mock_audit.emit.call_args[1]
        metadata = call_args["metadata"]

        # Should have unknown audience when token is malformed
        assert metadata["original_aud"] == "unknown"
        assert metadata["exchanged_aud"] == "unknown"

        # Should still have masked tokens
        assert "..." in metadata["token_1_masked"]
        assert "..." in metadata["token_2_masked"]

    @patch("request_manager.audit_token_tracker.jwt")
    @patch("request_manager.audit_token_tracker.AuditService")
    async def test_audit_default_outcome_is_success(self, mock_audit, mock_jwt):
        """Audit event defaults to success outcome when not specified."""
        # Mock JWT decode
        def decode_side_effect(token, **kwargs):
            if "original" in token:
                return {"sub": "user", "aud": "app1"}
            else:
                return {"sub": "user", "aud": "app2"}

        mock_jwt.decode.side_effect = decode_side_effect

        # Create test tokens
        original_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.original.sig"
        exchanged_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.exchanged.sig"

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Log without specifying outcome
        await TokenAuditTracker.log_token_exchange(
            original_token=original_token,
            exchanged_token=exchanged_token,
            source_agent="service-a",
            target_agent="service-b",
            delegation_chain=["service-a"],
        )

        # Verify outcome defaults to success
        call_args = mock_audit.emit.call_args[1]
        assert call_args["outcome"] == "success"

    @patch("request_manager.audit_token_tracker.jwt")
    @patch("request_manager.audit_token_tracker.AuditService")
    async def test_audit_failure_outcome(self, mock_audit, mock_jwt):
        """Audit event can log failure outcomes."""
        # Mock JWT decode
        def decode_side_effect(token, **kwargs):
            if "original" in token:
                return {"sub": "user", "aud": "app1"}
            else:
                return {"sub": "user", "aud": "app2"}

        mock_jwt.decode.side_effect = decode_side_effect

        # Create test tokens
        original_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.original.sig"
        exchanged_token = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.exchanged.sig"

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Log with failure outcome
        await TokenAuditTracker.log_token_exchange(
            original_token=original_token,
            exchanged_token=exchanged_token,
            source_agent="service-a",
            target_agent="service-b",
            delegation_chain=["service-a"],
            outcome="failure",
            reason="Invalid target audience"
        )

        # Verify failure outcome and reason
        call_args = mock_audit.emit.call_args[1]
        assert call_args["outcome"] == "failure"
        assert call_args["reason"] == "Invalid target audience"
