"""
RFC 8693 Token Exchange for Agent-to-Agent (A2A) Authentication.

This module implements OAuth 2.0 Token Exchange (RFC 8693) to enable secure
agent-to-agent communication with audience-specific JWTs and cryptographic
delegation chains.

When one agent needs to invoke another, it exchanges the user's original token
for a new token with:
- Audience (aud) scoped to the target agent
- Act claim proving the delegation chain
- Original user claims preserved

This ensures:
- Each agent receives a token valid ONLY for itself
- Delegation is cryptographically verifiable (not just HTTP headers)
- Token replay attacks are limited to a single agent
- Full audit trail of token exchanges

Example:
    >>> client = TokenExchangeClient()
    >>> result = await client.exchange_for_agent(
    ...     subject_token="eyJhbGci...",
    ...     target_agent="kubernetes-agent",
    ...     actor_service="routing-agent"
    ... )
    >>> print(result["access_token"])  # New token with aud: kubernetes-agent

Nested delegation example:
    >>> # First delegation: user -> agent-a
    >>> token1 = await client.exchange_with_delegation(
    ...     subject_token=user_token,
    ...     target_agent="agent-a",
    ...     actor_service="gateway"
    ... )
    >>> # Second delegation: agent-a -> agent-b (preserves chain)
    >>> token2 = await client.exchange_with_delegation(
    ...     subject_token=token1["access_token"],
    ...     target_agent="agent-b",
    ...     actor_service="agent-a",
    ...     existing_act_claim={"sub": "gateway"}
    ... )

Reference:
    - RFC 8693: https://www.rfc-editor.org/rfc/rfc8693.html
    - docs/A2A-TOKEN-FLOW.md: Architecture and implementation guide
"""

import json
import os
from typing import Any, Dict, Optional, Tuple

import httpx
import jwt
from shared_models import configure_logging
from shared_models.audit import AuditService
from shared_models.dcr_client import DCR_ENABLED

logger = configure_logging("request-manager.token-exchange")

# Keycloak configuration
KEYCLOAK_URL: str = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_REALM: str = os.getenv("KEYCLOAK_REALM", "partner-agent")
KEYCLOAK_CLIENT_ID: str = os.getenv("KEYCLOAK_CLIENT_ID", "partner-agent-ui")
KEYCLOAK_CLIENT_SECRET: str = os.getenv("KEYCLOAK_CLIENT_SECRET", "")


async def _get_dcr_actor_token(token_endpoint: str) -> Optional[str]:
    """Get a service-identity token from Keycloak using DCR client credentials.

    When DCR_ENABLED=true, uses the per-service DCR-registered client_id and
    client_secret to get a service account token via client_credentials grant.
    This token is used as the RFC 8693 actor_token in the exchange request,
    cryptographically proving which service is performing the delegation.

    The exchange itself still authenticates with partner-agent-ui (which is in
    the user token's audience) but the act claim in the new token will contain
    the DCR client's identity — giving each service a unique, traceable footprint
    in the delegation chain without requiring Keycloak fine-grained authorization.

    Returns the actor token string, or None if DCR is disabled / unavailable.
    """
    if not DCR_ENABLED:
        return None
    try:
        from shared_models.dcr_client import get_dcr_client
        from shared_models.spire_client import get_spire_client

        spire = get_spire_client()
        svid = spire.fetch_svid()
        if not svid or not svid.spiffe_id:
            return None
        dcr = get_dcr_client(svid.spiffe_id, "request-manager")
        creds = dcr.get_credentials()
        if not creds:
            return None
        dcr_client_id, dcr_client_secret = creds
        async with httpx.AsyncClient(timeout=10.0) as http:
            resp = await http.post(
                token_endpoint,
                data={
                    "grant_type": "client_credentials",
                    "client_id": dcr_client_id,
                    "client_secret": dcr_client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        if resp.status_code == 200:
            return resp.json().get("access_token")
        logger.debug(
            "DCR actor token request failed (HTTP %s) — proceeding without actor_token",
            resp.status_code,
        )
    except Exception as exc:
        logger.debug("DCR actor token fetch failed: %s", exc)
    return None

# RFC 8693 token type URNs
TOKEN_TYPE_ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"
TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"
GRANT_TYPE_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"


class TokenExchangeError(Exception):
    """Raised when token exchange fails."""

    def __init__(self, message: str, status_code: Optional[int] = None, detail: Optional[str] = None):
        """
        Initialize token exchange error.

        Args:
            message: Human-readable error message
            status_code: HTTP status code from Keycloak (if applicable)
            detail: Additional error details from Keycloak response
        """
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class TokenExchangeClient:
    """
    RFC 8693 Token Exchange client for Keycloak.

    This client exchanges user tokens for agent-specific tokens, enabling
    secure agent-to-agent communication with proper audience scoping and
    delegation tracking.

    The exchange flow:
    1. Agent A receives Token 1 (aud: "partner-agent-ui")
    2. Agent A needs to call Agent B
    3. Exchange Token 1 → Token 2 (aud: "agent-b", act: {sub: "agent-a"})
    4. Agent B validates Token 2's audience matches itself
    5. Agent B extracts delegation chain from act claim

    Attributes:
        token_endpoint: Keycloak's OAuth 2.0 token endpoint URL
        client: Async HTTP client for making requests
    """

    def __init__(
        self,
        keycloak_url: Optional[str] = None,
        realm: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """
        Initialize token exchange client.

        Args:
            keycloak_url: Keycloak base URL (defaults to KEYCLOAK_URL env var)
            realm: Keycloak realm name (defaults to KEYCLOAK_REALM env var)
            timeout: HTTP request timeout in seconds
        """
        self.keycloak_url = keycloak_url or KEYCLOAK_URL
        self.realm = realm or KEYCLOAK_REALM
        self.token_endpoint = (
            f"{self.keycloak_url}/realms/{self.realm}/protocol/openid-connect/token"
        )
        self.client = httpx.AsyncClient(timeout=timeout)

        logger.info(
            "Initialized TokenExchangeClient",
            keycloak_url=self.keycloak_url,
            realm=self.realm,
            token_endpoint=self.token_endpoint,
        )

    async def exchange_for_agent(
        self,
        subject_token: str,
        target_agent: str,
        actor_service: Optional[str] = None,
        requested_token_type: str = TOKEN_TYPE_ACCESS_TOKEN,
        existing_act_claim: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        """
        Exchange user token for agent-specific token.

        Performs RFC 8693 token exchange to obtain a new JWT with:
        - aud claim set to target_agent
        - act claim containing actor_service (if provided)
        - Original user claims (email, groups, etc.) preserved
        - New expiration time

        Args:
            subject_token: Original user JWT (Token 1)
            target_agent: Name of the target agent (becomes aud claim in Token 2)
            actor_service: Name of the acting service (becomes act.sub claim)
            requested_token_type: Type of token to request (default: access_token)
            existing_act_claim: Optional existing act claim to nest (builds nested delegation chain)

        Returns:
            Dictionary containing:
                - access_token: New JWT with target_agent audience
                - token_type: "Bearer"
                - expires_in: Token lifetime in seconds
                - refresh_token: (optional) Refresh token if issued
                - scope: (optional) Token scope if returned

        Raises:
            TokenExchangeError: If exchange fails (invalid token, unauthorized, etc.)
            httpx.TimeoutException: If request times out

        Example:
            >>> result = await client.exchange_for_agent(
            ...     subject_token="eyJhbGci...",
            ...     target_agent="kubernetes-agent",
            ...     actor_service="routing-agent"
            ... )
            >>> new_token = result["access_token"]  # Use for calling kubernetes-agent
        """
        logger.info(
            "Exchanging token for agent",
            target_agent=target_agent,
            actor_service=actor_service,
        )

        # Extract original aud claim from subject_token (decode without verification)
        # This is safe for audit logging - we just need to read the claims
        original_aud = None
        actor_claim = None
        try:
            unverified_payload = jwt.decode(
                subject_token,
                options={"verify_signature": False},
            )
            original_aud = unverified_payload.get("aud", "unknown")
            actor_claim = unverified_payload.get("act")
        except Exception as e:
            logger.warning(
                "Failed to decode subject_token for audit logging",
                error=str(e),
            )
            original_aud = "unknown"

        # Build RFC 8693 token exchange request.
        # The exchanger client MUST be in the subject_token audience — partner-agent-ui
        # is always in the audience because it issues the user's login token.
        # When DCR_ENABLED, we additionally fetch a service-identity actor_token using
        # the DCR client credentials; this proves which service is delegating and appears
        # in the "act" claim of the new token without requiring Keycloak fine-grained auth.
        auth_method = "legacy"
        actor_token_value: Optional[str] = None
        if DCR_ENABLED:
            actor_token_value = await _get_dcr_actor_token(self.token_endpoint)
            if actor_token_value:
                auth_method = "dcr-actor"

        payload = {
            "grant_type": GRANT_TYPE_TOKEN_EXCHANGE,
            "client_id": KEYCLOAK_CLIENT_ID,
            "subject_token": subject_token,
            "subject_token_type": TOKEN_TYPE_ACCESS_TOKEN,
            "requested_token_type": requested_token_type,
        }

        # Add client secret if configured (for confidential clients)
        if KEYCLOAK_CLIENT_SECRET:
            payload["client_secret"] = KEYCLOAK_CLIENT_SECRET

        # Include actor token for delegation chain (RFC 8693 §2.1)
        # When DCR is enabled: use the DCR service token as actor_token — the act claim
        # in the new token will contain the DCR client's sub, proving which service delegated.
        # Fallback: include actor_service as a plain string (non-standard but informative).
        if actor_token_value:
            payload["actor_token"] = actor_token_value
            payload["actor_token_type"] = TOKEN_TYPE_ACCESS_TOKEN
        elif existing_act_claim and actor_service:
            nested_act_claim = {
                "sub": actor_service,
                "act": existing_act_claim
            }
            payload["actor_token"] = json.dumps(nested_act_claim)
            logger.info(
                "Building nested act claim for delegation chain",
                actor_service=actor_service,
                has_existing_act=True,
            )
        elif actor_service:
            payload["actor_service"] = actor_service

        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

        logger.info(
            "Token exchange request — auth_method=%s client=%s actor_token=%s target=%s",
            auth_method,
            KEYCLOAK_CLIENT_ID,
            "DCR" if actor_token_value else "none",
            target_agent,
        )
        logger.debug(
            "Token exchange request details",
            token_endpoint=self.token_endpoint,
            grant_type=payload["grant_type"],
            client_id=payload["client_id"],
            auth_method=auth_method,
            has_client_secret=bool(payload.get("client_secret")),
            subject_token_length=len(subject_token) if subject_token else 0,
            subject_token_prefix=subject_token[:20] if subject_token else "None",
            requested_token_type=payload["requested_token_type"],
        )

        try:
            response = await self.client.post(
                self.token_endpoint,
                data=payload,
                headers=headers,
            )

            # Check for errors
            if response.status_code != 200:
                error_detail = self._extract_error_detail(response)
                logger.error(
                    "Token exchange failed",
                    status_code=response.status_code,
                    error_detail=error_detail,
                    target_agent=target_agent,
                )

                # Emit audit event for failed token exchange
                try:
                    await AuditService.emit(
                        event_type="token.exchange",
                        actor=actor_service or "unknown",
                        action="exchange_token",
                        resource=target_agent,
                        outcome="failure",
                        reason=f"HTTP {response.status_code}: {error_detail}",
                        metadata={
                            "original_aud": original_aud,
                            "new_aud": target_agent,
                            "act_claim": actor_claim,
                            "target_agent": target_agent,
                            "actor_service": actor_service,
                            "status_code": response.status_code,
                            "error_detail": error_detail,
                        },
                        service="request-manager",
                    )
                except Exception as audit_err:
                    logger.error(
                        "Failed to emit token exchange failure audit event",
                        error=str(audit_err),
                        target_agent=target_agent,
                    )

                raise TokenExchangeError(
                    f"Token exchange failed for {target_agent}",
                    status_code=response.status_code,
                    detail=error_detail,
                )

            result = response.json()

            # Validate response has required fields
            if "access_token" not in result:
                logger.error(
                    "Invalid token exchange response: missing access_token",
                    response_keys=list(result.keys()),
                )

                # Emit audit event for invalid response
                try:
                    await AuditService.emit(
                        event_type="token.exchange",
                        actor=actor_service or "unknown",
                        action="exchange_token",
                        resource=target_agent,
                        outcome="failure",
                        reason="Invalid token exchange response: missing access_token",
                        metadata={
                            "original_aud": original_aud,
                            "new_aud": target_agent,
                            "act_claim": actor_claim,
                            "target_agent": target_agent,
                            "actor_service": actor_service,
                            "response_keys": list(result.keys()),
                        },
                        service="request-manager",
                    )
                except Exception as audit_err:
                    logger.error(
                        "Failed to emit token exchange failure audit event",
                        error=str(audit_err),
                        target_agent=target_agent,
                    )

                raise TokenExchangeError(
                    "Invalid token exchange response: missing access_token"
                )

            logger.info(
                "Token exchange successful",
                target_agent=target_agent,
                token_type=result.get("token_type", "unknown"),
                expires_in=result.get("expires_in"),
                has_refresh_token=("refresh_token" in result),
            )

            # Emit audit event for successful token exchange
            try:
                await AuditService.emit(
                    event_type="token.exchange",
                    actor=actor_service or "unknown",
                    action="exchange_token",
                    resource=target_agent,
                    outcome="success",
                    reason=f"Token exchanged via RFC 8693 ({auth_method})",
                    metadata={
                        "original_aud": original_aud,
                        "new_aud": target_agent,
                        "act_claim": actor_claim,
                        "target_agent": target_agent,
                        "actor_service": actor_service,
                        "auth_method": auth_method,
                        "exchange_client_id": KEYCLOAK_CLIENT_ID,
                        "dcr_actor_token": bool(actor_token_value),
                        "token_type": result.get("token_type", "Bearer"),
                        "expires_in": result.get("expires_in"),
                    },
                    service="request-manager",
                )
            except Exception as audit_err:
                # Never fail the request due to audit logging
                logger.error(
                    "Failed to emit token exchange audit event",
                    error=str(audit_err),
                    target_agent=target_agent,
                )

            # Return standardized response
            return {
                "access_token": result["access_token"],
                "token_type": result.get("token_type", "Bearer"),
                "expires_in": result.get("expires_in", 300),  # Default 5 min
                **{
                    k: v
                    for k, v in result.items()
                    if k in ("refresh_token", "scope", "issued_token_type")
                },
            }

        except httpx.HTTPStatusError as e:
            error_detail = self._extract_error_detail(e.response)
            logger.error(
                "HTTP error during token exchange",
                status_code=e.response.status_code,
                error_detail=error_detail,
                target_agent=target_agent,
            )

            # Emit audit event for HTTP error
            try:
                await AuditService.emit(
                    event_type="token.exchange",
                    actor=actor_service or "unknown",
                    action="exchange_token",
                    resource=target_agent,
                    outcome="failure",
                    reason=f"HTTP {e.response.status_code}: {error_detail}",
                    metadata={
                        "original_aud": original_aud,
                        "new_aud": target_agent,
                        "act_claim": actor_claim,
                        "target_agent": target_agent,
                        "actor_service": actor_service,
                        "status_code": e.response.status_code,
                        "error_detail": error_detail,
                    },
                    service="request-manager",
                )
            except Exception as audit_err:
                logger.error(
                    "Failed to emit token exchange failure audit event",
                    error=str(audit_err),
                    target_agent=target_agent,
                )

            raise TokenExchangeError(
                f"HTTP {e.response.status_code} during token exchange",
                status_code=e.response.status_code,
                detail=error_detail,
            ) from e

        except httpx.TimeoutException as e:
            logger.error(
                "Timeout during token exchange",
                target_agent=target_agent,
                timeout=self.client.timeout.read,
            )

            # Emit audit event for timeout
            try:
                await AuditService.emit(
                    event_type="token.exchange",
                    actor=actor_service or "unknown",
                    action="exchange_token",
                    resource=target_agent,
                    outcome="failure",
                    reason=f"Request timeout after {self.client.timeout.read}s",
                    metadata={
                        "original_aud": original_aud,
                        "new_aud": target_agent,
                        "act_claim": actor_claim,
                        "target_agent": target_agent,
                        "actor_service": actor_service,
                        "timeout": self.client.timeout.read,
                    },
                    service="request-manager",
                )
            except Exception as audit_err:
                logger.error(
                    "Failed to emit token exchange failure audit event",
                    error=str(audit_err),
                    target_agent=target_agent,
                )

            raise

        except httpx.HTTPError as e:
            logger.error(
                "Network error during token exchange",
                error=str(e),
                error_type=type(e).__name__,
                target_agent=target_agent,
            )

            # Emit audit event for network error
            try:
                await AuditService.emit(
                    event_type="token.exchange",
                    actor=actor_service or "unknown",
                    action="exchange_token",
                    resource=target_agent,
                    outcome="failure",
                    reason=f"Network error: {type(e).__name__}: {str(e)}",
                    metadata={
                        "original_aud": original_aud,
                        "new_aud": target_agent,
                        "act_claim": actor_claim,
                        "target_agent": target_agent,
                        "actor_service": actor_service,
                        "error_type": type(e).__name__,
                    },
                    service="request-manager",
                )
            except Exception as audit_err:
                logger.error(
                    "Failed to emit token exchange failure audit event",
                    error=str(audit_err),
                    target_agent=target_agent,
                )

            raise TokenExchangeError(
                f"Network error during token exchange: {e}",
            ) from e

    async def exchange_with_delegation(
        self,
        subject_token: str,
        target_agent: str,
        actor_service: str,
        existing_act_claim: Optional[Dict[str, Any]] = None,
        requested_token_type: str = TOKEN_TYPE_ACCESS_TOKEN,
    ) -> Dict[str, Any]:
        """
        Exchange token with explicit delegation chain support.

        This is a convenience method that handles nested act claims for multi-hop
        delegation scenarios. It automatically extracts the act claim from the
        subject token if not provided, and builds the nested delegation chain.

        When Agent A (with token from User) calls Agent B, which then calls Agent C,
        the delegation chain looks like:
        - Token 1: {aud: "agent-a", act: {sub: "user"}}
        - Token 2: {aud: "agent-b", act: {sub: "agent-a", act: {sub: "user"}}}
        - Token 3: {aud: "agent-c", act: {sub: "agent-b", act: {sub: "agent-a", act: {sub: "user"}}}}

        Args:
            subject_token: Current JWT to exchange
            target_agent: Target agent name (becomes aud claim)
            actor_service: Current actor service name (becomes act.sub claim)
            existing_act_claim: Optional existing act claim to nest. If not provided,
                               will be extracted from subject_token
            requested_token_type: Type of token to request (default: access_token)

        Returns:
            Dictionary containing:
                - access_token: New JWT with nested act claim
                - token_type: "Bearer"
                - expires_in: Token lifetime in seconds
                - refresh_token: (optional) Refresh token if issued
                - scope: (optional) Token scope if returned
                - delegation_chain: List of actors in the delegation chain (newest first)

        Raises:
            TokenExchangeError: If exchange fails (invalid token, unauthorized, etc.)
            httpx.TimeoutException: If request times out

        Example:
            >>> # First hop: gateway -> agent-a
            >>> token1 = await client.exchange_with_delegation(
            ...     subject_token=user_token,
            ...     target_agent="agent-a",
            ...     actor_service="gateway"
            ... )
            >>>
            >>> # Second hop: agent-a -> agent-b (preserves gateway in chain)
            >>> token2 = await client.exchange_with_delegation(
            ...     subject_token=token1["access_token"],
            ...     target_agent="agent-b",
            ...     actor_service="agent-a"
            ... )
            >>> # token2 has act: {sub: "agent-a", act: {sub: "gateway"}}
        """
        logger.info(
            "Exchanging token with delegation chain",
            target_agent=target_agent,
            actor_service=actor_service,
            has_explicit_act_claim=existing_act_claim is not None,
        )

        # Extract existing act claim from subject_token if not explicitly provided
        act_claim_to_use = existing_act_claim
        if act_claim_to_use is None:
            try:
                unverified_payload = jwt.decode(
                    subject_token,
                    options={"verify_signature": False},
                )
                act_claim_to_use = unverified_payload.get("act")
                if act_claim_to_use:
                    logger.info(
                        "Extracted existing act claim from subject token",
                        extracted_act_claim=act_claim_to_use,
                    )
            except Exception as e:
                logger.warning(
                    "Failed to extract act claim from subject_token",
                    error=str(e),
                )
                # Continue without existing act claim - will create simple delegation

        # Perform token exchange with nested act claim
        result = await self.exchange_for_agent(
            subject_token=subject_token,
            target_agent=target_agent,
            actor_service=actor_service,
            requested_token_type=requested_token_type,
            existing_act_claim=act_claim_to_use,
        )

        # Extract and include delegation chain for convenience
        delegation_chain = self._extract_delegation_chain(result["access_token"])
        result["delegation_chain"] = delegation_chain

        logger.info(
            "Token exchange with delegation successful",
            target_agent=target_agent,
            delegation_chain_length=len(delegation_chain),
            delegation_chain=delegation_chain,
        )

        return result

    def _extract_delegation_chain(self, token: str) -> list[str]:
        """
        Extract the full delegation chain from a token's act claim.

        Walks the nested act claim structure to build a list of all actors
        in the delegation chain, from newest (current actor) to oldest (original user).

        Args:
            token: JWT token to extract chain from

        Returns:
            List of actor names in delegation order (newest first)
            Empty list if no act claim present

        Example:
            >>> # Token with act: {sub: "agent-b", act: {sub: "agent-a", act: {sub: "gateway"}}}
            >>> chain = client._extract_delegation_chain(token)
            >>> print(chain)
            ["agent-b", "agent-a", "gateway"]
        """
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
            act_claim = payload.get("act")

            if not act_claim:
                return []

            # Walk the nested act claim chain
            chain = []
            current = act_claim
            while current:
                if "sub" in current:
                    chain.append(current["sub"])
                current = current.get("act")

            return chain
        except Exception as e:
            logger.warning(
                "Failed to extract delegation chain from token",
                error=str(e),
            )
            return []

    def _extract_error_detail(self, response: httpx.Response) -> str:
        """
        Extract error details from Keycloak error response.

        Args:
            response: HTTP response from Keycloak

        Returns:
            Human-readable error description
        """
        try:
            if response.headers.get("content-type", "").startswith("application/json"):
                error_data = response.json()
                # Keycloak returns "error" and "error_description" per OAuth 2.0
                if "error_description" in error_data:
                    return error_data["error_description"]
                if "error" in error_data:
                    return error_data["error"]
                return str(error_data)
            return response.text[:200]  # First 200 chars if not JSON
        except Exception:
            return f"HTTP {response.status_code}"

    async def close(self) -> None:
        """Close the HTTP client and release resources."""
        await self.client.aclose()
        logger.debug("Closed TokenExchangeClient")

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
