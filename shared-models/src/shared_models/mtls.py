"""mTLS support for SPIFFE-based service-to-service communication.

Provides SSL context factories for uvicorn (server) and httpx (client),
plus an asyncio background task that refreshes SVID files before expiry.
"""

import asyncio
import logging
import os
import ssl
from typing import Optional

logger = logging.getLogger(__name__)

SVID_DIR_DEFAULT = "/run/spire/svids"

SVID_CERT = "svid.0.pem"
SVID_KEY = "svid.0.key"
SVID_BUNDLE = "bundle.0.pem"


def get_svid_dir() -> str:
    return os.getenv("SVID_DIR", SVID_DIR_DEFAULT)


def svid_paths(svid_dir: Optional[str] = None) -> dict[str, str]:
    d = svid_dir or get_svid_dir()
    return {
        "cert": os.path.join(d, SVID_CERT),
        "key": os.path.join(d, SVID_KEY),
        "bundle": os.path.join(d, SVID_BUNDLE),
    }


def _check_svid_files(paths: dict[str, str]) -> None:
    for name, path in paths.items():
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"SVID {name} file not found at {path}. "
                "Run write_svid_files() or mount SPIFFE CSI volume first."
            )


def server_ssl_context(svid_dir: Optional[str] = None) -> ssl.SSLContext:
    """Build an SSL context for uvicorn (TLS server with client-cert request).

    Uses CERT_OPTIONAL so health probes without certs still connect,
    but authenticated clients get identity extracted from peercert.
    """
    paths = svid_paths(svid_dir)
    _check_svid_files(paths)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=paths["cert"], keyfile=paths["key"])
    ctx.load_verify_locations(cafile=paths["bundle"])
    ctx.verify_mode = ssl.CERT_OPTIONAL
    ctx.check_hostname = False
    # SPIFFE uses URI SANs, not hostnames
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    logger.info("Built server SSL context from %s", svid_dir or get_svid_dir())
    return ctx


def client_ssl_context(svid_dir: Optional[str] = None) -> ssl.SSLContext:
    """Build an SSL context for httpx (TLS client presenting SVID cert)."""
    paths = svid_paths(svid_dir)
    _check_svid_files(paths)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.load_cert_chain(certfile=paths["cert"], keyfile=paths["key"])
    ctx.load_verify_locations(cafile=paths["bundle"])
    ctx.check_hostname = False
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    logger.info("Built client SSL context from %s", svid_dir or get_svid_dir())
    return ctx


async def svid_refresh_task(
    svid_dir: Optional[str] = None,
    interval: int = 3600,
) -> None:
    """Background task that periodically re-fetches SVID files from SPIRE.

    For Docker deployments where the SPIFFE CSI driver is not available.
    In OpenShift with CSI, the driver handles rotation automatically.
    """
    from .spire_client import get_spire_client

    d = svid_dir or get_svid_dir()
    client = get_spire_client()

    while True:
        await asyncio.sleep(interval)
        try:
            client.write_svid_files(d)
            logger.info("Refreshed SVID files in %s", d)
        except Exception:
            logger.exception("SVID refresh failed — will retry in %ds", interval)


def httpx_mtls_kwargs() -> dict:
    """Return extra kwargs for httpx.AsyncClient when SPIFFE_MODE=mtls.

    Service-to-service calls pass ``**httpx_mtls_kwargs()`` to present
    the SVID client certificate.  Returns empty dict in non-mtls modes.
    """
    mode = os.getenv("SPIFFE_MODE", "mock").lower()
    if mode != "mtls":
        return {}
    try:
        ctx = client_ssl_context()
        return {"verify": ctx}
    except FileNotFoundError:
        logger.warning("SPIFFE_MODE=mtls but SVID files not found — no client TLS")
        return {}
