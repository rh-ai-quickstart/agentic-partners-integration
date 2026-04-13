"""Tests for request_manager.credential_service."""

import pytest

from request_manager.credential_service import CredentialService


class TestCredentialService:
    """Tests for CredentialService context-variable management."""

    def setup_method(self):
        """Ensure a clean credential context before each test."""
        CredentialService.clear_credentials()

    def teardown_method(self):
        """Clean up credentials after each test to prevent cross-test pollution."""
        CredentialService.clear_credentials()

    # -- set / get user_id --------------------------------------------------

    def test_set_and_get_user_id(self):
        """set_user_id stores the value retrievable by get_user_id."""
        CredentialService.set_user_id("alice@example.com")
        assert CredentialService.get_user_id() == "alice@example.com"

    def test_get_user_id_default_none(self):
        """get_user_id returns None when no user_id has been set."""
        assert CredentialService.get_user_id() is None

    # -- set / get token ----------------------------------------------------

    def test_set_and_get_token(self):
        """set_token stores the value retrievable by get_token."""
        CredentialService.set_token("Bearer abc123")
        assert CredentialService.get_token() == "Bearer abc123"

    def test_get_token_default_none(self):
        """get_token returns None when no token has been set."""
        assert CredentialService.get_token() is None

    # -- set / get session_id -----------------------------------------------

    def test_set_and_get_session_id(self):
        """set_session_id stores the value retrievable by get_session_id."""
        CredentialService.set_session_id("session-xyz")
        assert CredentialService.get_session_id() == "session-xyz"

    def test_get_session_id_default_none(self):
        """get_session_id returns None when no session_id has been set."""
        assert CredentialService.get_session_id() is None

    # -- clear_credentials --------------------------------------------------

    def test_clear_credentials_resets_all(self):
        """clear_credentials sets every context variable back to None."""
        CredentialService.set_user_id("user1")
        CredentialService.set_token("tok")
        CredentialService.set_session_id("sess")

        CredentialService.clear_credentials()

        assert CredentialService.get_user_id() is None
        assert CredentialService.get_token() is None
        assert CredentialService.get_session_id() is None

    def test_clear_credentials_idempotent(self):
        """Calling clear_credentials when already clear does not error."""
        CredentialService.clear_credentials()
        assert CredentialService.get_user_id() is None

    # -- get_auth_header ----------------------------------------------------

    def test_get_auth_header_with_bearer_token(self):
        """When token already has 'Bearer ' prefix, return as-is."""
        CredentialService.set_token("Bearer mytoken123")
        assert CredentialService.get_auth_header() == "Bearer mytoken123"

    def test_get_auth_header_without_bearer_prefix(self):
        """When token lacks 'Bearer ' prefix, prepend it."""
        CredentialService.set_token("rawtoken456")
        assert CredentialService.get_auth_header() == "Bearer rawtoken456"

    def test_get_auth_header_no_token(self):
        """When no token is set, get_auth_header returns None."""
        assert CredentialService.get_auth_header() is None

    # -- overwrite behaviour ------------------------------------------------

    def test_overwrite_user_id(self):
        """Setting user_id twice overwrites the previous value."""
        CredentialService.set_user_id("first")
        CredentialService.set_user_id("second")
        assert CredentialService.get_user_id() == "second"

    def test_overwrite_token(self):
        """Setting token twice overwrites the previous value."""
        CredentialService.set_token("tok1")
        CredentialService.set_token("tok2")
        assert CredentialService.get_token() == "tok2"
