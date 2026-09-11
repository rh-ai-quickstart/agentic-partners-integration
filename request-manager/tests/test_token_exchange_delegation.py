"""Tests for token exchange delegation chain functionality.

This module tests RFC 8693 token exchange delegation scenarios:
- First-hop delegation (user -> agent)
- Multi-hop delegation chains (agent -> agent -> agent)
- Preservation of user claims across hops
- Audience scoping per agent
- Error handling with context preservation
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import jwt

from request_manager.token_exchange import (
    TokenExchangeClient,
    TokenExchangeError,
    TOKEN_TYPE_ACCESS_TOKEN,
)


def create_test_token(
    aud: str,
    email: str = "user@example.com",
    groups: list[str] = None,
    act_claim: dict = None,
    sub: str = "user-123",
) -> str:
    """
    Create a test JWT token with specified claims.

    Args:
        aud: Audience claim
        email: User email
        groups: User groups
        act_claim: Delegation act claim
        sub: Subject claim

    Returns:
        Encoded JWT token (without signature verification for testing)
    """
    if groups is None:
        groups = ["users", "developers"]

    payload = {
        "aud": aud,
        "sub": sub,
        "email": email,
        "groups": groups,
        "exp": 9999999999,  # Far future expiration
        "iat": 1609459200,
    }

    if act_claim:
        payload["act"] = act_claim

    # Encode without signature for testing (algorithm="none" is not secure but works for tests)
    # Using HS256 with a dummy key for testing
    return jwt.encode(payload, "test-secret", algorithm="HS256")


@pytest.mark.asyncio
class TestTokenExchangeDelegation:
    """Tests for delegation chain functionality in token exchange."""

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_exchange_without_delegation(self, mock_httpx, mock_audit):
        """First hop: User token exchanged for agent token without existing delegation."""
        # Create user token (no act claim - first hop)
        user_token = create_test_token(
            aud="partner-agent-ui",
            email="alice@example.com",
            groups=["users", "admin"],
        )

        # Mock Keycloak response with new token
        new_token = create_test_token(
            aud="agent-a",
            email="alice@example.com",
            groups=["users", "admin"],
            act_claim={"sub": "gateway"},
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Exchange token
        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token=user_token,
            target_agent="agent-a",
            actor_service="gateway",
        )

        # Verify result
        assert result["access_token"] == new_token
        assert result["token_type"] == "Bearer"
        assert result["expires_in"] == 300

        # Verify Keycloak was called with correct parameters
        call_args = mock_client.post.call_args
        assert call_args[0][0].endswith("/protocol/openid-connect/token")

        payload = call_args[1]["data"]
        assert payload["subject_token"] == user_token
        assert payload["audience"] == "agent-a"
        assert payload["actor_service"] == "gateway"
        assert "actor_token" not in payload  # No existing act claim

        # Verify new token has act claim
        decoded = jwt.decode(new_token, options={"verify_signature": False})
        assert decoded["aud"] == "agent-a"
        assert decoded["act"]["sub"] == "gateway"

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_exchange_with_existing_act(self, mock_httpx, mock_audit):
        """Second hop: Builds nested act claim from existing delegation."""
        # Create token with existing act claim (from first hop)
        token_with_act = create_test_token(
            aud="agent-a",
            email="alice@example.com",
            act_claim={"sub": "gateway"},
        )

        # Mock Keycloak response with nested act claim
        new_token = create_test_token(
            aud="agent-b",
            email="alice@example.com",
            act_claim={
                "sub": "agent-a",
                "act": {"sub": "gateway"},
            },
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Exchange token with existing act claim
        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token=token_with_act,
            target_agent="agent-b",
            actor_service="agent-a",
            existing_act_claim={"sub": "gateway"},
        )

        # Verify result
        assert result["access_token"] == new_token

        # Verify Keycloak was called with nested act claim
        call_args = mock_client.post.call_args
        payload = call_args[1]["data"]

        assert payload["audience"] == "agent-b"
        assert "actor_token" in payload

        # Parse the actor_token (nested act claim)
        actor_token_data = json.loads(payload["actor_token"])
        assert actor_token_data["sub"] == "agent-a"
        assert actor_token_data["act"]["sub"] == "gateway"

        # Verify new token has nested act claim
        decoded = jwt.decode(new_token, options={"verify_signature": False})
        assert decoded["aud"] == "agent-b"
        assert decoded["act"]["sub"] == "agent-a"
        assert decoded["act"]["act"]["sub"] == "gateway"

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_exchange_preserves_user_claims(self, mock_httpx, mock_audit):
        """User claims (email, groups) are preserved across delegation hops."""
        # Original user token with specific claims
        user_email = "bob@example.com"
        user_groups = ["users", "developers", "k8s-admin"]

        user_token = create_test_token(
            aud="partner-agent-ui",
            email=user_email,
            groups=user_groups,
        )

        # Mock response preserves user claims
        new_token = create_test_token(
            aud="kubernetes-agent",
            email=user_email,
            groups=user_groups,
            act_claim={"sub": "routing-agent"},
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Exchange token
        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token=user_token,
            target_agent="kubernetes-agent",
            actor_service="routing-agent",
        )

        # Verify user claims are preserved in new token
        decoded = jwt.decode(result["access_token"], options={"verify_signature": False})
        assert decoded["email"] == user_email
        assert decoded["groups"] == user_groups
        assert decoded["aud"] == "kubernetes-agent"  # Audience changed
        assert decoded["act"]["sub"] == "routing-agent"  # Delegation added

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_exchange_updates_audience(self, mock_httpx, mock_audit):
        """Audience claim is updated for target agent on each exchange."""
        # Token with original audience
        original_token = create_test_token(aud="agent-a")

        # Mock response with new audience
        new_token = create_test_token(
            aud="agent-b",
            email="user@example.com",
            act_claim={"sub": "agent-a"},
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": new_token,
            "token_type": "Bearer",
            "expires_in": 300,
        }

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Exchange token
        client = TokenExchangeClient()
        result = await client.exchange_for_agent(
            subject_token=original_token,
            target_agent="agent-b",
            actor_service="agent-a",
        )

        # Verify audience was updated
        original_decoded = jwt.decode(original_token, options={"verify_signature": False})
        new_decoded = jwt.decode(result["access_token"], options={"verify_signature": False})

        assert original_decoded["aud"] == "agent-a"
        assert new_decoded["aud"] == "agent-b"

        # Verify audit event captured the audience change
        audit_call_args = mock_audit.emit.call_args[1]
        assert audit_call_args["metadata"]["original_aud"] == "agent-a"
        assert audit_call_args["metadata"]["new_aud"] == "agent-b"

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_multiple_exchanges_build_chain(self, mock_httpx, mock_audit):
        """Three-hop delegation chain: user -> agent-a -> agent-b -> agent-c."""
        # Mock audit service
        mock_audit.emit = AsyncMock()

        # Hop 1: user -> agent-a
        user_token = create_test_token(
            aud="partner-agent-ui",
            email="charlie@example.com",
            groups=["users"],
        )

        token_a = create_test_token(
            aud="agent-a",
            email="charlie@example.com",
            groups=["users"],
            act_claim={"sub": "gateway"},
        )

        # Hop 2: agent-a -> agent-b
        token_b = create_test_token(
            aud="agent-b",
            email="charlie@example.com",
            groups=["users"],
            act_claim={
                "sub": "agent-a",
                "act": {"sub": "gateway"},
            },
        )

        # Hop 3: agent-b -> agent-c
        token_c = create_test_token(
            aud="agent-c",
            email="charlie@example.com",
            groups=["users"],
            act_claim={
                "sub": "agent-b",
                "act": {
                    "sub": "agent-a",
                    "act": {"sub": "gateway"},
                },
            },
        )

        # Mock HTTP client to return different tokens for each hop
        responses = [
            MagicMock(status_code=200, json=MagicMock(return_value={
                "access_token": token_a,
                "token_type": "Bearer",
                "expires_in": 300,
            })),
            MagicMock(status_code=200, json=MagicMock(return_value={
                "access_token": token_b,
                "token_type": "Bearer",
                "expires_in": 300,
            })),
            MagicMock(status_code=200, json=MagicMock(return_value={
                "access_token": token_c,
                "token_type": "Bearer",
                "expires_in": 300,
            })),
        ]

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=responses)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        client = TokenExchangeClient()

        # Hop 1: user -> agent-a
        result_a = await client.exchange_with_delegation(
            subject_token=user_token,
            target_agent="agent-a",
            actor_service="gateway",
        )

        # Verify hop 1
        decoded_a = jwt.decode(result_a["access_token"], options={"verify_signature": False})
        assert decoded_a["aud"] == "agent-a"
        assert decoded_a["act"]["sub"] == "gateway"
        assert result_a["delegation_chain"] == ["gateway"]

        # Hop 2: agent-a -> agent-b
        result_b = await client.exchange_with_delegation(
            subject_token=result_a["access_token"],
            target_agent="agent-b",
            actor_service="agent-a",
        )

        # Verify hop 2
        decoded_b = jwt.decode(result_b["access_token"], options={"verify_signature": False})
        assert decoded_b["aud"] == "agent-b"
        assert decoded_b["act"]["sub"] == "agent-a"
        assert decoded_b["act"]["act"]["sub"] == "gateway"
        assert result_b["delegation_chain"] == ["agent-a", "gateway"]

        # Hop 3: agent-b -> agent-c
        result_c = await client.exchange_with_delegation(
            subject_token=result_b["access_token"],
            target_agent="agent-c",
            actor_service="agent-b",
        )

        # Verify hop 3 - full chain
        decoded_c = jwt.decode(result_c["access_token"], options={"verify_signature": False})
        assert decoded_c["aud"] == "agent-c"
        assert decoded_c["act"]["sub"] == "agent-b"
        assert decoded_c["act"]["act"]["sub"] == "agent-a"
        assert decoded_c["act"]["act"]["act"]["sub"] == "gateway"
        assert result_c["delegation_chain"] == ["agent-b", "agent-a", "gateway"]

        # Verify email preserved through all hops
        assert decoded_c["email"] == "charlie@example.com"
        assert decoded_c["groups"] == ["users"]

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    @patch("request_manager.token_exchange.httpx.AsyncClient")
    async def test_exchange_failure_preserves_context(self, mock_httpx, mock_audit):
        """Failed exchange emits audit event and preserves error context for fallback."""
        # Create token for exchange
        test_token = create_test_token(
            aud="agent-a",
            email="dave@example.com",
            act_claim={"sub": "gateway"},
        )

        # Mock Keycloak error response
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "error": "insufficient_permissions",
            "error_description": "User does not have permission to access agent-b",
        }
        mock_response.text = "User does not have permission to access agent-b"

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        mock_httpx.return_value = mock_client

        # Mock audit service
        mock_audit.emit = AsyncMock()

        client = TokenExchangeClient()

        # Attempt exchange - should fail
        with pytest.raises(TokenExchangeError) as exc_info:
            await client.exchange_for_agent(
                subject_token=test_token,
                target_agent="agent-b",
                actor_service="agent-a",
            )

        # Verify error preserves context
        error = exc_info.value
        assert error.status_code == 403
        assert "agent-b" in str(error)
        assert error.detail == "User does not have permission to access agent-b"

        # Verify audit event was emitted with failure context
        assert mock_audit.emit.called
        audit_call_args = mock_audit.emit.call_args[1]

        assert audit_call_args["event_type"] == "token.exchange"
        assert audit_call_args["outcome"] == "failure"
        assert audit_call_args["resource"] == "agent-b"
        assert "403" in audit_call_args["reason"]

        # Verify metadata preserves original context for fallback
        metadata = audit_call_args["metadata"]
        assert metadata["original_aud"] == "agent-a"
        assert metadata["new_aud"] == "agent-b"
        assert metadata["target_agent"] == "agent-b"
        assert metadata["actor_service"] == "agent-a"
        assert metadata["status_code"] == 403
        assert metadata["act_claim"]["sub"] == "gateway"

        # Verify original token is still usable for fallback
        # (In real scenario, caller could retry with different target or escalate)
        original_decoded = jwt.decode(test_token, options={"verify_signature": False})
        assert original_decoded["aud"] == "agent-a"
        assert original_decoded["email"] == "dave@example.com"

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    async def test_delegation_chain_extraction_empty(self, mock_audit):
        """Delegation chain extraction returns empty list for token without act claim."""
        # Token without act claim
        token_no_act = create_test_token(
            aud="agent-a",
            email="test@example.com",
        )

        client = TokenExchangeClient()
        chain = client._extract_delegation_chain(token_no_act)

        assert chain == []

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    async def test_delegation_chain_extraction_single_hop(self, mock_audit):
        """Delegation chain extraction returns single actor for one-hop delegation."""
        token_single_hop = create_test_token(
            aud="agent-a",
            email="test@example.com",
            act_claim={"sub": "gateway"},
        )

        client = TokenExchangeClient()
        chain = client._extract_delegation_chain(token_single_hop)

        assert chain == ["gateway"]

        await client.close()

    @patch("request_manager.token_exchange.AuditService")
    async def test_delegation_chain_extraction_nested(self, mock_audit):
        """Delegation chain extraction walks nested act claims correctly."""
        token_nested = create_test_token(
            aud="agent-c",
            email="test@example.com",
            act_claim={
                "sub": "agent-b",
                "act": {
                    "sub": "agent-a",
                    "act": {"sub": "gateway"},
                },
            },
        )

        client = TokenExchangeClient()
        chain = client._extract_delegation_chain(token_nested)

        assert chain == ["agent-b", "agent-a", "gateway"]

        await client.close()
