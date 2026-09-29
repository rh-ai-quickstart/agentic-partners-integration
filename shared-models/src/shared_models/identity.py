"""
SPIFFE Workload Identity for Python/FastAPI services.

SPIFFE_MODE controls identity trust and outbound behavior:
  - ``mock``: trust X-SPIFFE-ID header (inbound), hardcoded identity (outbound)
  - ``spire-header``: trust X-SPIFFE-ID header (inbound, NetworkPolicy-guarded),
    real SVID from SPIRE (outbound)
  - ``mtls``: only trust mTLS peer cert (inbound), real SVID from SPIRE (outbound)
"""

import logging
import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Request

logger = logging.getLogger(__name__)

TRUST_DOMAIN: str = os.getenv("SPIFFE_TRUST_DOMAIN", "partner.example.com")
SPIFFE_MODE: str = os.getenv("SPIFFE_MODE", "mock").lower()

from .spire_client import get_spire_client


@dataclass
class WorkloadIdentity:
    """Represents a SPIFFE workload identity (X509-SVID or mock)."""

    spiffe_id: str

    @property
    def entity_type(self) -> str:
        """Extract entity type from SPIFFE ID path (e.g. 'user', 'agent', 'service')."""
        parts = self.spiffe_id.rstrip("/").split("/")
        return parts[-2] if len(parts) >= 2 else "unknown"

    @property
    def name(self) -> str:
        """Extract entity name from SPIFFE ID (last path segment)."""
        return self.spiffe_id.rstrip("/").split("/")[-1]


def make_spiffe_id(entity_type: str, name: str) -> str:
    """Build a SPIFFE ID from entity type and name.

    Examples:
        make_spiffe_id("user", "alice") -> "spiffe://partner.example.com/user/alice"
        make_spiffe_id("service", "request-manager") -> "spiffe://partner.example.com/service/request-manager"
    """
    return f"spiffe://{TRUST_DOMAIN}/{entity_type}/{name}"


def extract_identity(request: Request) -> Optional[WorkloadIdentity]:
    """Extract workload identity from an incoming request.

    mTLS peer certificate is always checked first (all modes).
    In ``mock`` and ``spire-header`` modes the X-SPIFFE-ID header is trusted
    as a fallback.  In ``mtls`` mode the header is ignored to prevent spoofing.
    """
    scope = request.scope
    transport = scope.get("transport")
    if transport is not None:
        peercert = transport.get_extra_info("peercert")
        if peercert:
            san = peercert.get("subjectAltName", ())
            for san_type, san_value in san:
                if san_type == "URI" and san_value.startswith("spiffe://"):
                    return WorkloadIdentity(spiffe_id=san_value)

    if SPIFFE_MODE in ("mock", "spire-header"):
        spiffe_id = request.headers.get("X-SPIFFE-ID")
        if spiffe_id:
            return WorkloadIdentity(spiffe_id=spiffe_id)

    return None


def outbound_identity_headers(
    service_name: str,
    delegation_user: Optional[str] = None,
    delegation_agent: Optional[str] = None,
    request_id: Optional[str] = None,
) -> dict[str, str]:
    """Build identity headers for outgoing service-to-service requests.

    Fetches real X.509-SVID from SPIRE Agent and sets X-SPIFFE-ID header.
    In full mTLS setup, identity would be in the client certificate.

    Args:
        service_name: Name of the calling service (e.g. "request-manager")
        delegation_user: SPIFFE ID of the user who delegated access (optional)
        delegation_agent: SPIFFE ID of the agent acting on behalf of user (optional)
        request_id: Correlation ID propagated across service calls (optional)

    Raises:
        RuntimeError: If SPIRE SVID fetch fails (production - no fallback allowed)
    """
    headers: dict[str, str] = {}

    if SPIFFE_MODE == "mock":
        spiffe_id = make_spiffe_id("service", service_name)
        headers["X-SPIFFE-ID"] = spiffe_id
        logger.info(f"Using mock SPIFFE identity: {spiffe_id}")
    elif SPIFFE_MODE == "mtls":
        # In mTLS mode identity is in the client certificate — no header needed.
        logger.debug("mTLS mode: identity will be in client certificate, no header set")
    else:
        try:
            client = get_spire_client()
            svid_info = client.fetch_svid()

            if not svid_info:
                raise RuntimeError(
                    f"Failed to fetch SVID from SPIRE for service '{service_name}'. "
                    "SPIRE integration is REQUIRED - no fallback allowed."
                )

            headers["X-SPIFFE-ID"] = svid_info.spiffe_id
            logger.info(f"Using real SPIRE SVID: {svid_info.spiffe_id}")

        except Exception as e:
            logger.error(f"SPIRE SVID fetch failed for '{service_name}': {e}")
            raise RuntimeError(
                f"Cannot obtain SVID from SPIRE for service '{service_name}': {e}. "
                "SPIRE integration is REQUIRED - no fallback allowed."
            ) from e

    if delegation_user:
        headers["X-Delegation-User"] = delegation_user
    if delegation_agent:
        headers["X-Delegation-Agent"] = delegation_agent
    if request_id:
        headers["X-Request-ID"] = request_id

    return headers
