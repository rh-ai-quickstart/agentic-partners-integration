"""
SPIFFE Workload Identity for Python/FastAPI services.

Provides mock and real SPIFFE identity extraction, matching the pattern
from zero-trust-agent-demo/pkg/spiffe/workload.go.

In mock mode (MOCK_SPIFFE=true, the default), identity is carried via
the X-SPIFFE-ID header. In real mode, identity is extracted from mTLS
peer certificate SANs.
"""

import os
from dataclasses import dataclass
from typing import Optional

from fastapi import Request

# Configuration from environment
MOCK_SPIFFE: bool = os.getenv("MOCK_SPIFFE", "true").lower() == "true"
TRUST_DOMAIN: str = os.getenv("SPIFFE_TRUST_DOMAIN", "partner.example.com")


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

    In mock mode: reads the X-SPIFFE-ID header.
    In real mode: extracts SPIFFE ID from the mTLS peer certificate SAN.
    """
    if MOCK_SPIFFE:
        spiffe_id = request.headers.get("X-SPIFFE-ID")
        if spiffe_id:
            return WorkloadIdentity(spiffe_id=spiffe_id)
        return None

    # Real mode: extract from mTLS peer certificate
    # The ASGI server (uvicorn with ssl) populates the TLS info
    # via the transport's get_extra_info("peercert")
    scope = request.scope
    transport = scope.get("transport")
    if transport is not None:
        peercert = transport.get_extra_info("peercert")
        if peercert:
            # Extract SPIFFE ID from SAN (URI type)
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

    In mock mode, sets X-SPIFFE-ID header with the service's identity.
    In real mode, mTLS handles identity — only delegation headers are added.

    Args:
        service_name: Name of the calling service (e.g. "request-manager")
        delegation_user: SPIFFE ID of the user who delegated access (optional)
        delegation_agent: SPIFFE ID of the agent acting on behalf of user (optional)
    """
    headers: dict[str, str] = {}

    if MOCK_SPIFFE:
        headers["X-SPIFFE-ID"] = make_spiffe_id("service", service_name)

    if delegation_user:
        headers["X-Delegation-User"] = delegation_user
    if delegation_agent:
        headers["X-Delegation-Agent"] = delegation_agent

    return headers
