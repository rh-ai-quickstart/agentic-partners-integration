"""Tests for shared_models.spire_client module — 100% coverage.

Tests cover:
- SVIDInfo dataclass and __repr__
- SPIREClient initialization (socket_path, cli_path params)
- SPIREClient.write_svid_files() — all branches
- SPIREClient._find_cli() with os.path.exists checks
- SPIREClient.fetch_svid() — all branches
- SPIREClient.fetch_jwt_svid() — all branches
- SPIREClient.is_available() — success and failure
- get_spire_client() singleton
"""

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from shared_models.spire_client import SPIREClient, SVIDInfo, get_spire_client

# ── SVIDInfo ────────────────────────────────────────────────────────────────

class TestSVIDInfo:
    def test_repr(self):
        svid = SVIDInfo(
            spiffe_id="spiffe://example.com/service/test",
            valid_after="2025-01-01",
            valid_until="2025-12-31",
        )
        assert repr(svid) == "SVIDInfo(spiffe_id='spiffe://example.com/service/test')"


# ── SPIREClient.__init__ ────────────────────────────────────────────────────

class TestSPIREClientInit:
    def test_defaults_from_env(self, monkeypatch):
        monkeypatch.setenv("SPIFFE_ENDPOINT_SOCKET", "unix:///tmp/test.sock")
        with patch.object(SPIREClient, "_find_cli", return_value=None):
            client = SPIREClient()
        assert client.socket_path == "/tmp/test.sock"
        assert client.cli_path is None

    def test_explicit_params(self):
        client = SPIREClient(
            socket_path="/custom/socket.sock",
            cli_path="/custom/bin/spire-agent",
        )
        assert client.socket_path == "/custom/socket.sock"
        assert client.cli_path == "/custom/bin/spire-agent"

    def test_default_socket_path(self, monkeypatch):
        monkeypatch.delenv("SPIFFE_ENDPOINT_SOCKET", raising=False)
        with patch.object(SPIREClient, "_find_cli", return_value=None):
            client = SPIREClient()
        assert client.socket_path == "/run/spire/sockets/agent.sock"


# ── SPIREClient._find_cli ───────────────────────────────────────────────────

class TestFindCli:
    def test_finds_first_existing(self):
        def exists_side_effect(path):
            return path == "/usr/local/bin/spire-agent"

        with patch("os.path.exists", side_effect=exists_side_effect):
            client = SPIREClient(socket_path="/tmp/s.sock")
        assert client.cli_path == "/usr/local/bin/spire-agent"

    def test_returns_none_when_no_cli_found(self):
        with patch("os.path.exists", return_value=False):
            client = SPIREClient(socket_path="/tmp/s.sock")
        assert client.cli_path is None

    def test_finds_first_location(self):
        """Should return the first match (/opt/spire/bin/spire-agent)."""
        with patch("os.path.exists", return_value=True):
            client = SPIREClient(socket_path="/tmp/s.sock")
        assert client.cli_path == "/opt/spire/bin/spire-agent"


# ── SPIREClient.fetch_svid ──────────────────────────────────────────────────

class TestFetchSvid:
    def test_raises_if_no_cli(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path=None)
        with pytest.raises(RuntimeError, match="spire-agent CLI not found"):
            client.fetch_svid()

    def test_raises_if_socket_not_found(self):
        client = SPIREClient(socket_path="/nonexistent/sock", cli_path="/usr/bin/spire-agent")
        with patch("os.path.exists", return_value=False):
            with pytest.raises(RuntimeError, match="SPIRE socket not found"):
                client.fetch_svid()

    def test_success(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = (
            "SPIFFE ID: spiffe://example.com/service/test\n"
            "SVID Valid After: 2025-01-01T00:00:00Z\n"
            "SVID Valid Until: 2025-12-31T23:59:59Z\n"
        )
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                svid = client.fetch_svid()
        assert svid.spiffe_id == "spiffe://example.com/service/test"
        assert svid.valid_after == "2025-01-01T00:00:00Z"
        assert svid.valid_until == "2025-12-31T23:59:59Z"

    def test_success_missing_validity(self):
        """When validity timestamps are missing, defaults to 'unknown'."""
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "SPIFFE ID: spiffe://example.com/service/test\n"
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                svid = client.fetch_svid()
        assert svid.valid_after == "unknown"
        assert svid.valid_until == "unknown"

    def test_nonzero_returncode(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "connection refused"
        mock_result.stdout = ""
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with pytest.raises(RuntimeError, match="SPIRE CLI fetch failed"):
                    client.fetch_svid()

    def test_nonzero_returncode_stderr_empty(self):
        """When stderr is empty, uses stdout for error message."""
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = ""
        mock_result.stdout = "some error output"
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with pytest.raises(RuntimeError, match="SPIRE CLI fetch failed"):
                    client.fetch_svid()

    def test_missing_spiffe_id_in_output(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Some output without SPIFFE ID\n"
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with pytest.raises(RuntimeError, match="Could not parse SPIFFE ID"):
                    client.fetch_svid()

    def test_timeout(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="spire-agent", timeout=10)):
                with pytest.raises(RuntimeError, match="timed out"):
                    client.fetch_svid()

    def test_generic_exception(self):
        """Generic exceptions in fetch_svid are wrapped in RuntimeError."""
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", side_effect=OSError("permission denied")):
                with pytest.raises(RuntimeError, match="SPIRE SVID fetch failed"):
                    client.fetch_svid()


# ── SPIREClient.fetch_jwt_svid ──────────────────────────────────────────────

class TestFetchJwtSvid:
    def test_raises_if_no_cli(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path=None)
        with pytest.raises(RuntimeError, match="spire-agent CLI not found"):
            client.fetch_jwt_svid("https://keycloak/dcr")

    def test_raises_if_socket_not_found(self):
        client = SPIREClient(socket_path="/nonexistent/sock", cli_path="/usr/bin/spire-agent")
        with patch("os.path.exists", return_value=False):
            with pytest.raises(RuntimeError, match="SPIRE socket not found"):
                client.fetch_jwt_svid("https://keycloak/dcr")

    def test_success_token_prefix(self):
        """Parses JWT from 'token: <jwt>' line."""
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "token: eyJhbGciOiJSUzI1NiJ9.payload.sig\n"
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with patch("shared_models.spire_client.logger"):
                    jwt = client.fetch_jwt_svid("https://keycloak/dcr")
        assert jwt == "eyJhbGciOiJSUzI1NiJ9.payload.sig"

    def test_success_raw_ey_line(self):
        """Parses JWT from a raw 'ey...' line (no 'token:' prefix)."""
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "eyJhbGciOiJSUzI1NiJ9.raw.jwt\n"
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with patch("shared_models.spire_client.logger"):
                    jwt = client.fetch_jwt_svid("https://keycloak/dcr")
        assert jwt == "eyJhbGciOiJSUzI1NiJ9.raw.jwt"

    def test_nonzero_returncode(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "fetch error"
        mock_result.stdout = ""
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with pytest.raises(RuntimeError, match="SPIRE CLI fetch failed"):
                    client.fetch_jwt_svid("https://keycloak/dcr")

    def test_nonzero_returncode_stderr_empty(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = ""
        mock_result.stdout = "stdout error"
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with pytest.raises(RuntimeError, match="SPIRE CLI fetch failed"):
                    client.fetch_jwt_svid("https://keycloak/dcr")

    def test_parse_failure(self):
        """No JWT found in output."""
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "SPIFFE ID: spiffe://example.com/service/test\n"
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with pytest.raises(RuntimeError, match="Could not parse JWT-SVID"):
                    client.fetch_jwt_svid("https://keycloak/dcr")

    def test_timeout(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="spire-agent", timeout=10)):
                with pytest.raises(RuntimeError, match="timed out"):
                    client.fetch_jwt_svid("https://keycloak/dcr")

    def test_generic_exception(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", side_effect=OSError("permission denied")):
                with pytest.raises(RuntimeError, match="SPIRE JWT-SVID fetch failed"):
                    client.fetch_jwt_svid("https://keycloak/dcr")


# ── SPIREClient.write_svid_files ────────────────────────────────────────────

class TestWriteSvidFiles:
    def test_raises_if_no_cli(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path=None)
        with pytest.raises(RuntimeError, match="spire-agent CLI not found"):
            client.write_svid_files("/tmp/svids")

    def test_raises_if_socket_not_found(self):
        client = SPIREClient(socket_path="/nonexistent/sock", cli_path="/usr/bin/spire-agent")
        with patch("os.path.exists", return_value=False):
            with pytest.raises(RuntimeError, match="SPIRE socket not found"):
                client.write_svid_files("/tmp/svids")

    def test_success(self, tmp_path):
        svid_dir = str(tmp_path / "svids")
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Writing SVID files\n"

        import os
        def run_side_effect(*args, **kwargs):
            os.makedirs(svid_dir, exist_ok=True)
            for f in ["svid.0.pem", "svid.0.key", "bundle.0.pem"]:
                (tmp_path / "svids" / f).write_text("cert-data")
            return mock_result

        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", side_effect=run_side_effect):
                paths = client.write_svid_files(svid_dir)

        assert paths["cert"].endswith("svid.0.pem")
        assert paths["key"].endswith("svid.0.key")
        assert paths["bundle"].endswith("bundle.0.pem")

    def test_nonzero_returncode(self, tmp_path):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "write failed"
        mock_result.stdout = ""
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", return_value=mock_result):
                with pytest.raises(RuntimeError, match="SPIRE CLI write failed"):
                    client.write_svid_files(str(tmp_path / "svids"))

    def test_missing_output_file(self, tmp_path):
        svid_dir = str(tmp_path / "svids")
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_result = MagicMock()
        mock_result.returncode = 0

        def run_side_effect(*args, **kwargs):
            os.makedirs(svid_dir, exist_ok=True)
            # Only create cert — key and bundle missing → should fail
            (tmp_path / "svids" / "svid.0.pem").write_text("cert")
            return mock_result

        real_exists = os.path.exists
        with patch("os.path.exists", side_effect=lambda p: p == client.socket_path or real_exists(p)):
            with patch("subprocess.run", side_effect=run_side_effect):
                with pytest.raises(RuntimeError, match="file missing"):
                    client.write_svid_files(svid_dir)

    def test_timeout(self, tmp_path):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="spire-agent", timeout=10)):
                with pytest.raises(RuntimeError, match="timed out"):
                    client.write_svid_files(str(tmp_path / "svids"))

    def test_generic_exception(self, tmp_path):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        with patch("os.path.exists", return_value=True):
            with patch("subprocess.run", side_effect=OSError("permission denied")):
                with pytest.raises(RuntimeError, match="SPIRE SVID write failed"):
                    client.write_svid_files(str(tmp_path / "svids"))


# ── SPIREClient.is_available ────────────────────────────────────────────────

class TestIsAvailable:
    def test_returns_true_when_svid_fetched(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        mock_svid = SVIDInfo(
            spiffe_id="spiffe://example.com/service/test",
            valid_after="2025-01-01",
            valid_until="2025-12-31",
        )
        with patch.object(client, "fetch_svid", return_value=mock_svid):
            assert client.is_available() is True

    def test_returns_false_on_exception(self):
        client = SPIREClient(socket_path="/tmp/s.sock", cli_path="/usr/bin/spire-agent")
        with patch.object(client, "fetch_svid", side_effect=RuntimeError("no SPIRE")):
            assert client.is_available() is False


# ── get_spire_client singleton ──────────────────────────────────────────────

class TestGetSpireClient:
    def test_returns_singleton(self):
        import shared_models.spire_client as mod
        mod._spire_client = None
        try:
            with patch.object(SPIREClient, "_find_cli", return_value=None):
                c1 = get_spire_client()
                c2 = get_spire_client()
            assert c1 is c2
        finally:
            mod._spire_client = None

    def test_creates_instance(self):
        import shared_models.spire_client as mod
        mod._spire_client = None
        try:
            with patch.object(SPIREClient, "_find_cli", return_value=None):
                client = get_spire_client()
            assert isinstance(client, SPIREClient)
        finally:
            mod._spire_client = None
