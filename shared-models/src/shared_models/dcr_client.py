"""OAuth 2.0 Dynamic Client Registration (RFC 7591) client.

Enables agents to self-register with Keycloak on startup using a
Keycloak Initial Access Token (IAT) created during seeding, eliminating
the need for pre-seeded per-agent Keycloak clients.

When DCR_ENABLED=true (the default), each agent calls
``ensure_registered()`` at startup.  If the agent is already registered
(RAT cached in memory), Keycloak confirms the registration with a quick
GET.  If not, it POSTs to the DCR endpoint using the IAT as the
Authorization bearer.  Keycloak creates the client and returns a
Registration Access Token for future management operations.

The IAT is read from KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN (set by
seed-services.sh which reads it from /tmp/dcr_initial_access_token.txt
written by seed-keycloak.sh).

Feature flag: DCR_ENABLED (default true).  Set to false only for local
development without Keycloak, or during the brief transition period when
migrating from legacy client_secret auth.
"""


import asyncio
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ── Feature flag ──────────────────────────────────────────────────────────────
# Default True — agents self-register on startup using Keycloak IAT.
# Set DCR_ENABLED=false only for local dev without Keycloak.
DCR_ENABLED = os.getenv("DCR_ENABLED", "true").lower() == "true"

# ── Keycloak configuration ────────────────────────────────────────────────────
_KEYCLOAK_BASE = os.getenv("KEYCLOAK_URL", "http://keycloak:8090").rstrip("/")
_KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "partner-agent")
_DCR_ENDPOINT = os.getenv(
    "KEYCLOAK_DCR_ENDPOINT",
    f"{_KEYCLOAK_BASE}/realms/{_KEYCLOAK_REALM}"
    "/clients-registrations/openid-connect",
)
# Initial Access Token created by seed-keycloak.sh — agents use this as
# the Authorization bearer when posting the first DCR registration.
_DCR_INITIAL_ACCESS_TOKEN = os.getenv("KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN", "")
_SPIRE_OIDC_JWKS = os.getenv(
    "SPIRE_OIDC_JWKS_URI",
    "http://spire-oidc-provider:8443/.well-known/jwks.json",
)
# Retry config for startup race with Keycloak cold-start
_DCR_MAX_RETRIES = int(os.getenv("DCR_MAX_RETRIES", "10"))
_DCR_RETRY_DELAY = float(os.getenv("DCR_RETRY_DELAY_SECONDS", "3"))

# Admin credentials for post-registration configuration (enabling legacy token exchange)
_KEYCLOAK_ADMIN_USERNAME = os.getenv("KEYCLOAK_ADMIN_USERNAME", "admin")
_KEYCLOAK_ADMIN_PASSWORD = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "")


class DCRClient:
    """Handles Dynamic Client Registration for a single workload identity.

    Intended to be instantiated once per agent process (singleton pattern).
    Not thread-safe for concurrent registrations from the same process —
    but agents are single-process, so this is not a concern in practice.

    Args:
        spiffe_id: The agent's full SPIFFE URI (e.g.
            ``spiffe://partner.example.com/agent/kubernetes-agent``).
        client_name: Human-readable name for the Keycloak client.
    """

    def __init__(self, spiffe_id: str, client_name: str) -> None:
        self._spiffe_id = spiffe_id
        self._client_name = client_name
        self._registration_access_token: Optional[str] = None
        self._registration_client_uri: Optional[str] = None
        # Stored after successful DCR registration — used for token exchange
        self._keycloak_client_id: Optional[str] = None
        self._keycloak_client_secret: Optional[str] = None

    async def ensure_registered(self) -> None:
        """Ensure this agent is registered as a Keycloak DCR client.

        No-op when DCR_ENABLED=false.

        On first call (or after a stale RAT): POSTs a DCR registration to
        Keycloak using the Initial Access Token from KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN.
        Keycloak creates the client and returns a Registration Access Token.

        On subsequent calls within the process lifetime: verifies the cached
        RAT is still accepted by Keycloak.  Re-registers on 401/404.

        Retries up to DCR_MAX_RETRIES times with DCR_RETRY_DELAY_SECONDS between
        attempts to handle Keycloak cold-start race conditions.
        """
        if not DCR_ENABLED:
            return

        if self._registration_access_token and self._registration_client_uri:
            if await self._verify_registration():
                logger.debug(
                    "DCR registration still valid",
                    spiffe_id=self._spiffe_id,
                )
                return

        last_exc: Optional[Exception] = None
        for attempt in range(1, _DCR_MAX_RETRIES + 1):
            try:
                await self._register()
                return
            except Exception as exc:
                last_exc = exc
                if attempt < _DCR_MAX_RETRIES:
                    logger.warning(
                        "DCR registration attempt failed — retrying",
                        spiffe_id=self._spiffe_id,
                        attempt=attempt,
                        max_retries=_DCR_MAX_RETRIES,
                        retry_delay=_DCR_RETRY_DELAY,
                        error=str(exc),
                    )
                    await asyncio.sleep(_DCR_RETRY_DELAY)

        raise RuntimeError(
            f"DCR registration failed after {_DCR_MAX_RETRIES} attempts "
            f"for {self._spiffe_id}: {last_exc}"
        ) from last_exc

    async def _enable_legacy_token_exchange(self) -> None:
        """No-op: token exchange uses the RFC 8693 actor_token pattern, not direct DCR exchange.

        The DCR client's credentials are used via client_credentials grant to obtain a
        service-account token, which is then passed as actor_token in the exchange request.
        The exchange itself is authenticated by partner-agent-ui (which is in the user token
        audience). No special Keycloak permissions are required for this pattern.
        """

    def get_credentials(self) -> Optional[tuple[str, str]]:
        """Return (keycloak_client_id, keycloak_client_secret) if registered, else None.

        Used by token_exchange.py to authenticate with DCR-issued credentials
        instead of the shared partner-agent-ui client_secret.
        """
        if self._keycloak_client_id and self._keycloak_client_secret:
            return (self._keycloak_client_id, self._keycloak_client_secret)
        return None

    async def _verify_registration(self) -> bool:
        """Return True if the current RAT is still accepted by Keycloak."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    self._registration_client_uri,
                    headers={"Authorization": f"Bearer {self._registration_access_token}"},
                )
                return resp.status_code == 200
        except Exception:
            return False

    async def _register(self) -> None:
        """POST RFC 7591 registration to Keycloak using the Initial Access Token.

        The IAT was created by seed-keycloak.sh and is passed via
        KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN.  The registered client uses
        private_key_jwt auth backed by the SPIRE OIDC JWKS endpoint so the
        agent authenticates with its SVID private key — no stored secrets.
        """
        if not _DCR_INITIAL_ACCESS_TOKEN:
            raise RuntimeError(
                "Cannot DCR-register: KEYCLOAK_DCR_INITIAL_ACCESS_TOKEN is not set. "
                "Ensure seed-keycloak.sh ran successfully before starting agents."
            )

        # client_id is NOT included — Keycloak generates a UUID.
        # The SPIFFE URI is stored in client_name (human-readable label)
        # and in the "spiffe_id" attribute for our own lookup.
        # Keycloak's DCR endpoint rejects user-supplied client_id values
        # (error: "Client Identifier included").
        payload = {
            "client_name": f"{self._client_name} ({self._spiffe_id})",
            "grant_types": [
                "client_credentials",
                "urn:ietf:params:oauth:grant-type:token-exchange",
            ],
            "redirect_uris": [],
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    _DCR_ENDPOINT,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {_DCR_INITIAL_ACCESS_TOKEN}",
                    },
                )
        except httpx.RequestError as e:
            raise RuntimeError(
                f"DCR registration request failed (network error): {e}"
            ) from e

        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"Keycloak DCR registration failed "
                f"(HTTP {resp.status_code}): {resp.text[:500]}"
            )

        data = resp.json()
        self._registration_access_token = data.get("registration_access_token")
        self._registration_client_uri = data.get("registration_client_uri")
        self._keycloak_client_id = data.get("client_id")
        self._keycloak_client_secret = data.get("client_secret")

        if not self._registration_access_token:
            raise RuntimeError(
                "Keycloak DCR response missing registration_access_token. "
                f"Response: {data}"
            )

        logger.info(
            "DCR registration successful — spiffe_id=%s keycloak_client=%s",
            self._spiffe_id,
            self._keycloak_client_id,
        )

        # Keycloak 26+: DCR registers with standard.token.exchange.enabled (strict audience check).
        # Switch to oauth2.token.exchange.grant.enabled (legacy, permissive) so the DCR client
        # can exchange tokens issued for other clients (e.g. partner-agent-ui).
        await self._enable_legacy_token_exchange()


# ── Singleton factory ─────────────────────────────────────────────────────────
_dcr_clients: dict[str, DCRClient] = {}


def get_dcr_client(spiffe_id: str, client_name: str) -> DCRClient:
    """Return a cached DCRClient for the given SPIFFE ID.

    Creates a new instance on first call for a given spiffe_id.
    Thread-safety is not a concern (single-process agents).
    """
    if spiffe_id not in _dcr_clients:
        _dcr_clients[spiffe_id] = DCRClient(
            spiffe_id=spiffe_id,
            client_name=client_name,
        )
    return _dcr_clients[spiffe_id]
