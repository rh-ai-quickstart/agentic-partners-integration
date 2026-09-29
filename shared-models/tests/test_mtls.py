"""Tests for shared_models.mtls module — SSL contexts and SVID helpers."""

import asyncio
import os
import ssl
from unittest.mock import MagicMock, patch

import pytest

from shared_models.mtls import (
    _check_svid_files,
    client_ssl_context,
    get_svid_dir,
    httpx_mtls_kwargs,
    server_ssl_context,
    svid_paths,
    svid_refresh_task,
)

# ── get_svid_dir ───────────────────────────────────────────────────────────

class TestGetSvidDir:
    def test_default(self, monkeypatch):
        monkeypatch.delenv("SVID_DIR", raising=False)
        assert get_svid_dir() == "/run/spire/svids"

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("SVID_DIR", "/custom/svids")
        assert get_svid_dir() == "/custom/svids"


# ── svid_paths ─────────────────────────────────────────────────────────────

class TestSvidPaths:
    def test_explicit_dir(self):
        paths = svid_paths("/my/dir")
        assert paths["cert"] == "/my/dir/svid.0.pem"
        assert paths["key"] == "/my/dir/svid.0.key"
        assert paths["bundle"] == "/my/dir/bundle.0.pem"

    def test_default_dir(self, monkeypatch):
        monkeypatch.delenv("SVID_DIR", raising=False)
        paths = svid_paths()
        assert paths["cert"] == "/run/spire/svids/svid.0.pem"


# ── _check_svid_files ──────────────────────────────────────────────────────

class TestCheckSvidFiles:
    def test_all_exist(self, tmp_path):
        for f in ["svid.0.pem", "svid.0.key", "bundle.0.pem"]:
            (tmp_path / f).write_text("data")
        paths = svid_paths(str(tmp_path))
        _check_svid_files(paths)

    def test_missing_cert(self, tmp_path):
        (tmp_path / "svid.0.key").write_text("data")
        (tmp_path / "bundle.0.pem").write_text("data")
        paths = svid_paths(str(tmp_path))
        with pytest.raises(FileNotFoundError, match="cert"):
            _check_svid_files(paths)

    def test_missing_key(self, tmp_path):
        (tmp_path / "svid.0.pem").write_text("data")
        (tmp_path / "bundle.0.pem").write_text("data")
        paths = svid_paths(str(tmp_path))
        with pytest.raises(FileNotFoundError, match="key"):
            _check_svid_files(paths)

    def test_missing_bundle(self, tmp_path):
        (tmp_path / "svid.0.pem").write_text("data")
        (tmp_path / "svid.0.key").write_text("data")
        paths = svid_paths(str(tmp_path))
        with pytest.raises(FileNotFoundError, match="bundle"):
            _check_svid_files(paths)


# ── SSL context builders ──────────────────────────────────────────────────

def _create_test_certs(tmp_path):
    """Create self-signed cert/key/bundle for testing SSL context creation."""
    from subprocess import run
    key_path = tmp_path / "svid.0.key"
    cert_path = tmp_path / "svid.0.pem"
    bundle_path = tmp_path / "bundle.0.pem"

    run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(key_path), "-out", str(cert_path),
        "-days", "1", "-nodes", "-subj", "/CN=test",
    ], capture_output=True, check=True)

    bundle_path.write_bytes(cert_path.read_bytes())
    return str(tmp_path)


class TestServerSslContext:
    def test_creates_context(self, tmp_path):
        svid_dir = _create_test_certs(tmp_path)
        ctx = server_ssl_context(svid_dir)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_OPTIONAL
        assert ctx.check_hostname is False

    def test_missing_files_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            server_ssl_context(str(tmp_path))

    def test_tls_minimum_version(self, tmp_path):
        svid_dir = _create_test_certs(tmp_path)
        ctx = server_ssl_context(svid_dir)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2


class TestClientSslContext:
    def test_creates_context(self, tmp_path):
        svid_dir = _create_test_certs(tmp_path)
        ctx = client_ssl_context(svid_dir)
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.check_hostname is False

    def test_missing_files_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            client_ssl_context(str(tmp_path))

    def test_tls_minimum_version(self, tmp_path):
        svid_dir = _create_test_certs(tmp_path)
        ctx = client_ssl_context(svid_dir)
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2


# ── httpx_mtls_kwargs ──────────────────────────────────────────────────────

class TestHttpxMtlsKwargs:
    def test_returns_empty_in_mock_mode(self, monkeypatch):
        monkeypatch.setenv("SPIFFE_MODE", "mock")
        assert httpx_mtls_kwargs() == {}

    def test_returns_empty_in_spire_header_mode(self, monkeypatch):
        monkeypatch.setenv("SPIFFE_MODE", "spire-header")
        assert httpx_mtls_kwargs() == {}

    def test_returns_context_in_mtls_mode(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPIFFE_MODE", "mtls")
        svid_dir = _create_test_certs(tmp_path)
        monkeypatch.setenv("SVID_DIR", svid_dir)
        kwargs = httpx_mtls_kwargs()
        assert "verify" in kwargs
        assert isinstance(kwargs["verify"], ssl.SSLContext)

    def test_returns_empty_if_files_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SPIFFE_MODE", "mtls")
        monkeypatch.setenv("SVID_DIR", str(tmp_path / "nonexistent"))
        kwargs = httpx_mtls_kwargs()
        assert kwargs == {}


# ── svid_refresh_task ──────────────────────────────────────────────────────

# ── Integration: server + client SSL contexts work together ────────────────

class TestMtlsIntegration:
    def test_server_and_client_contexts_handshake(self, tmp_path):
        """Server and client SSL contexts created from the same SVID can handshake."""
        svid_dir = _create_test_certs(tmp_path)
        srv_ctx = server_ssl_context(svid_dir)
        cli_ctx = client_ssl_context(svid_dir)

        import socket
        import threading

        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("127.0.0.1", 0))
        port = server_sock.getsockname()[1]
        server_sock.listen(1)

        handshake_ok = False

        def server_thread():
            nonlocal handshake_ok
            conn, _ = server_sock.accept()
            try:
                ssl_conn = srv_ctx.wrap_socket(conn, server_side=True)
                ssl_conn.recv(1)
                handshake_ok = True
                ssl_conn.close()
            except Exception:
                pass
            finally:
                server_sock.close()

        t = threading.Thread(target=server_thread, daemon=True)
        t.start()

        try:
            raw = socket.create_connection(("127.0.0.1", port), timeout=5)
            ssl_sock = cli_ctx.wrap_socket(raw, server_hostname="test")
            ssl_sock.send(b"x")
            ssl_sock.close()
        except Exception:
            pass

        t.join(timeout=5)
        assert handshake_ok, "TLS handshake between server and client SVID contexts failed"


# ── svid_refresh_task ──────────────────────────────────────────────────────

class TestSvidRefreshTask:
    @pytest.mark.asyncio
    async def test_refresh_calls_write(self, tmp_path):
        mock_client = MagicMock()
        with patch("shared_models.spire_client.get_spire_client", return_value=mock_client):
            task = asyncio.create_task(
                svid_refresh_task(str(tmp_path), interval=0)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        mock_client.write_svid_files.assert_called_with(str(tmp_path))

    @pytest.mark.asyncio
    async def test_refresh_continues_on_error(self, tmp_path):
        mock_client = MagicMock()
        mock_client.write_svid_files.side_effect = RuntimeError("SPIRE down")
        with patch("shared_models.spire_client.get_spire_client", return_value=mock_client):
            task = asyncio.create_task(
                svid_refresh_task(str(tmp_path), interval=0)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert mock_client.write_svid_files.call_count >= 1
