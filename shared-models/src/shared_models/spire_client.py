"""
Real SPIRE Workload API client - Production Implementation.

Uses SPIRE Agent CLI for reliable SVID fetching.
This approach is production-ready and avoids Python library issues.
"""

import os
import json
import subprocess
import logging
from typing import Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SVIDInfo:
    """SVID information extracted from X.509-SVID."""
    spiffe_id: str
    valid_after: str
    valid_until: str

    def __repr__(self):
        return f"SVIDInfo(spiffe_id='{self.spiffe_id}')"


class SPIREClient:
    """
    Production SPIRE client using SPIRE Agent CLI.

    Fetches X.509-SVIDs from SPIRE Agent via the agent CLI tool.
    This is a production-ready approach that bypasses Python library issues.

    Example:
        >>> client = SPIREClient()
        >>> svid_info = client.fetch_svid()
        >>> print(svid_info.spiffe_id)
        spiffe://partner.example.com/service/request-manager
    """

    def __init__(self, socket_path: Optional[str] = None, cli_path: Optional[str] = None):
        """
        Initialize SPIRE client.

        Args:
            socket_path: Path to SPIRE Agent socket
            cli_path: Path to spire-agent CLI tool
        """
        self.socket_path = socket_path or os.getenv(
            "SPIFFE_ENDPOINT_SOCKET",
            "/run/spire/sockets/agent.sock"
        ).replace("unix://", "")

        # Try to find spire-agent CLI
        self.cli_path = cli_path or self._find_cli()

    def _find_cli(self) -> Optional[str]:
        """Find spire-agent CLI in common locations."""
        locations = [
            "/opt/spire/bin/spire-agent",
            "/usr/local/bin/spire-agent",
            "/usr/bin/spire-agent",
        ]

        for path in locations:
            if os.path.exists(path):
                return path

        return None

    def fetch_svid(self) -> Optional[SVIDInfo]:
        """
        Fetch X.509-SVID from SPIRE Agent using CLI.

        Returns:
            SVIDInfo with SPIFFE ID and validity info, or None if unavailable

        Raises:
            RuntimeError: If SPIRE CLI not available or fetch fails
        """
        if not self.cli_path:
            raise RuntimeError(
                "spire-agent CLI not found. SPIRE integration requires the CLI tool."
            )

        if not os.path.exists(self.socket_path):
            raise RuntimeError(
                f"SPIRE socket not found at {self.socket_path}. "
                "Ensure SPIRE Agent is running."
            )

        try:
            # Run spire-agent api fetch command
            result = subprocess.run(
                [
                    self.cli_path,
                    "api", "fetch", "x509",
                    "-socketPath", self.socket_path,
                    "-timeout", "5s"
                ],
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode != 0:
                raise RuntimeError(
                    f"SPIRE CLI fetch failed: {result.stderr or result.stdout}"
                )

            # Parse output to extract SPIFFE ID
            output = result.stdout
            spiffe_id = None
            valid_after = None
            valid_until = None

            for line in output.split('\n'):
                if 'SPIFFE ID:' in line:
                    spiffe_id = line.split('SPIFFE ID:')[1].strip()
                elif 'SVID Valid After:' in line:
                    valid_after = line.split('SVID Valid After:')[1].strip()
                elif 'SVID Valid Until:' in line:
                    valid_until = line.split('SVID Valid Until:')[1].strip()

            if not spiffe_id:
                raise RuntimeError("Could not parse SPIFFE ID from SPIRE output")

            logger.info(f"Fetched SVID from SPIRE: {spiffe_id}")

            return SVIDInfo(
                spiffe_id=spiffe_id,
                valid_after=valid_after or "unknown",
                valid_until=valid_until or "unknown"
            )

        except subprocess.TimeoutExpired:
            raise RuntimeError("SPIRE CLI fetch timed out after 10 seconds")
        except Exception as e:
            logger.error(f"Failed to fetch SVID from SPIRE: {e}")
            raise RuntimeError(f"SPIRE SVID fetch failed: {e}") from e

    def fetch_jwt_svid(self, audience: str) -> str:
        """Fetch a JWT-SVID from SPIRE for the given audience.

        Returns a signed JWT string whose ``sub`` is this workload's SPIFFE
        URI and whose ``aud`` is the requested audience.  The JWT is signed
        by the SPIRE key whose public part is published at the SPIRE OIDC
        discovery endpoint, so recipients can verify it without contacting
        SPIRE directly.

        This is the credential used for Dynamic Client Registration (DCR):
        the agent presents this JWT to Keycloak's DCR endpoint to prove its
        identity without a pre-shared secret.

        Args:
            audience: The ``aud`` claim value (e.g. the Keycloak DCR URL).

        Returns:
            The JWT-SVID as a raw string (no "Bearer " prefix).

        Raises:
            RuntimeError: If the SPIRE CLI is not available, the socket is
                missing, or the fetch fails.
        """
        if not self.cli_path:
            raise RuntimeError(
                "spire-agent CLI not found. SPIRE integration requires the CLI tool."
            )
        if not os.path.exists(self.socket_path):
            raise RuntimeError(
                f"SPIRE socket not found at {self.socket_path}. "
                "Ensure SPIRE Agent is running."
            )

        try:
            result = subprocess.run(
                [
                    self.cli_path,
                    "api", "fetch", "jwt",
                    "-audience", audience,
                    "-socketPath", self.socket_path,
                    "-timeout", "5s",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"SPIRE CLI fetch failed: {result.stderr or result.stdout}"
                )

            # The CLI outputs one or more "token: <jwt>" lines.
            # We return the first JWT found.
            for line in result.stdout.split("\n"):
                line = line.strip()
                if line and not line.startswith("SPIFFE") and not line.startswith("token:"):
                    # Raw JWT line (no label prefix)
                    if line.startswith("ey"):
                        logger.info(
                            "Fetched JWT-SVID from SPIRE",
                            audience=audience,
                        )
                        return line
                if line.startswith("token:"):
                    jwt = line.split("token:", 1)[1].strip()
                    logger.info(
                        "Fetched JWT-SVID from SPIRE",
                        audience=audience,
                    )
                    return jwt

            raise RuntimeError(
                f"Could not parse JWT-SVID from SPIRE output: {result.stdout[:200]}"
            )

        except subprocess.TimeoutExpired:
            raise RuntimeError("SPIRE CLI JWT fetch timed out after 10 seconds")
        except Exception as e:
            logger.error(f"Failed to fetch JWT-SVID from SPIRE: {e}")
            raise RuntimeError(f"SPIRE JWT-SVID fetch failed: {e}") from e

    def is_available(self) -> bool:
        """
        Check if SPIRE Agent is available.

        Returns:
            True if can fetch SVID successfully
        """
        try:
            self.fetch_svid()
            return True
        except Exception:
            return False


# Singleton instance
_spire_client: Optional[SPIREClient] = None


def get_spire_client() -> SPIREClient:
    """
    Get or create the global SPIRE client instance.

    Returns:
        SPIREClient instance
    """
    global _spire_client
    if _spire_client is None:
        _spire_client = SPIREClient()
    return _spire_client


# For compatibility
SPIFFE_AVAILABLE = True
