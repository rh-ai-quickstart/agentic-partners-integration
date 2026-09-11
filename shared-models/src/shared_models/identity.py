"""
SPIFFE Workload Identity for Python/FastAPI services.

Production implementation using real SPIRE/SPIFFE.
Uses official spiffe library from https://pypi.org/project/spiffe/

Identity is fetched from SPIRE Agent via the Workload API (X.509-SVIDs).
"""

import os
from dataclasses import dataclass
from typing import Optional
import logging

from fastapi import Request

logger = logging.getLogger(__name__)

# Configuration from environment
TRUST_DOMAIN: str = os.getenv("SPIFFE_TRUST_DOMAIN", "partner.example.com")

# Import SPIRE client (production)
from .spire_client import get_spire_client, SPIFFE_AVAILABLE


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

    Reads the X-SPIFFE-ID header (caller's SVID identity).
    In production, this would be validated against mTLS certificate.
    """
    # Read caller's SPIFFE ID from header
    # (In full mTLS setup, this would be extracted from peer certificate)
    spiffe_id = request.headers.get("X-SPIFFE-ID")
    if spiffe_id:
        return WorkloadIdentity(spiffe_id=spiffe_id)

    # Fallback: try to extract from mTLS peer certificate
    scope = request.scope
    transport = scope.get("transport")
    if transport is not None:
        peercert = transport.get_extra_info("peercert")
        if peercert:
            san = peercert.get("subjectAltName", ())
            for san_type, san_value in san:
                if san_type == "URI" and san_value.startswith("spiffe://"):
                    return WorkloadIdentity(spiffe_id=san_value)

    return None


def outbound_identity_headers(
    service_name: str,
    delegation_user: Optional[str] = None,
    delegation_agent: Optional[str] = None,
) -> dict[str, str]:
    """Build identity headers for outgoing service-to-service requests.

    Fetches real X.509-SVID from SPIRE Agent and sets X-SPIFFE-ID header.
    In full mTLS setup, identity would be in the client certificate.

    Args:
        service_name: Name of the calling service (e.g. "request-manager")
        delegation_user: SPIFFE ID of the user who delegated access (optional)
        delegation_agent: SPIFFE ID of the agent acting on behalf of user (optional)

    Raises:
        RuntimeError: If SPIRE SVID fetch fails (production - no fallback allowed)
    """
    headers: dict[str, str] = {}

    # Fetch real SVID from SPIRE (PRODUCTION - NO MOCK ALLOWED)
    if not SPIFFE_AVAILABLE:
        raise RuntimeError(
            "SPIFFE library not available. Install with: pip install spiffe"
        )

    try:
        client = get_spire_client()
        svid_info = client.fetch_svid()

        if not svid_info:
            raise RuntimeError(
                f"Failed to fetch SVID from SPIRE for service '{service_name}'. "
                "SPIRE integration is REQUIRED - no fallback allowed."
            )

        # Set identity header with real SPIFFE ID from SVID
        headers["X-SPIFFE-ID"] = svid_info.spiffe_id
        logger.info(f"Using real SPIRE SVID: {svid_info.spiffe_id}")

    except Exception as e:
        # Production: FAIL LOUDLY - no fallback
        logger.error(f"SPIRE SVID fetch failed for '{service_name}': {e}")
        raise RuntimeError(
            f"Cannot obtain SVID from SPIRE for service '{service_name}': {e}. "
            "SPIRE integration is REQUIRED - no fallback allowed."
        ) from e

    if delegation_user:
        headers["X-Delegation-User"] = delegation_user
    if delegation_agent:
        headers["X-Delegation-Agent"] = delegation_agent

    return headers
