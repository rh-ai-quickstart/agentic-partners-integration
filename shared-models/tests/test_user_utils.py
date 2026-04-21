"""Tests for shared_models.user_utils module."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared_models.user_utils import (
    _ensure_email_mapping,
    get_or_create_canonical_user,
    is_uuid,
    resolve_canonical_user_id,
)


class TestIsUuid:
    """Tests for is_uuid()."""

    def test_valid_uuid_with_hyphens(self):
        assert is_uuid("550e8400-e29b-41d4-a716-446655440000") is True

    def test_valid_uuid_without_hyphens(self):
        assert is_uuid("550e8400e29b41d4a716446655440000") is True

    def test_generated_uuid(self):
        assert is_uuid(str(uuid.uuid4())) is True

    def test_uppercase_uuid(self):
        assert is_uuid("550E8400-E29B-41D4-A716-446655440000") is True

    def test_invalid_email(self):
        assert is_uuid("test@example.com") is False

    def test_invalid_random_text(self):
        assert is_uuid("not-a-uuid") is False

    def test_invalid_empty_string(self):
        assert is_uuid("") is False

    def test_invalid_short_hex(self):
        assert is_uuid("550e8400") is False


class TestResolveCanonicalUserId:
    """Tests for resolve_canonical_user_id()."""

    async def test_uuid_input_no_db(self):
        """UUID input without db should return as-is (backward compat)."""
        uid = str(uuid.uuid4())
        result = await resolve_canonical_user_id(uid)
        assert result == uid

    async def test_uuid_input_with_db_user_exists(self, mock_db_session):
        """UUID input with db, user exists, should return UUID."""
        uid = str(uuid.uuid4())
        mock_user = MagicMock()
        mock_user.user_id = uid

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db_session.execute.return_value = mock_result

        result = await resolve_canonical_user_id(uid, db=mock_db_session)
        assert result == uid

    async def test_email_input_raises_without_db(self):
        """Email input without db should raise ValueError."""
        with pytest.raises(ValueError, match="Database session required"):
            await resolve_canonical_user_id("test@example.com")

    @patch("shared_models.user_utils.get_or_create_canonical_user")
    async def test_email_input_with_db(self, mock_get_or_create, mock_db_session):
        """Email input with db should resolve via get_or_create_canonical_user."""
        expected_uid = str(uuid.uuid4())
        mock_get_or_create.return_value = expected_uid

        result = await resolve_canonical_user_id("test@example.com", db=mock_db_session)

        assert result == expected_uid
        mock_get_or_create.assert_called_once_with("test@example.com", mock_db_session)

    async def test_uuid_input_with_db_user_not_exists(self, mock_db_session):
        """UUID input where user doesn't exist should create user."""
        uid = str(uuid.uuid4())

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        result = await resolve_canonical_user_id(uid, db=mock_db_session)

        assert result == uid
        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_called_once()

    async def test_uuid_input_with_db_user_not_exists_race_condition(
        self, mock_db_session
    ):
        """UUID input where concurrent creation leads to constraint error then lookup."""
        uid = str(uuid.uuid4())

        # First execute: user does not exist
        mock_result_none = MagicMock()
        mock_result_none.scalar_one_or_none.return_value = None

        # flush raises a unique constraint error
        mock_db_session.flush = AsyncMock(
            side_effect=Exception("unique constraint violation")
        )

        # Second execute (retry lookup): user now exists
        mock_user = MagicMock()
        mock_user.user_id = uid
        mock_result_found = MagicMock()
        mock_result_found.scalar_one_or_none.return_value = mock_user

        mock_db_session.execute = AsyncMock(
            side_effect=[mock_result_none, mock_result_found]
        )

        result = await resolve_canonical_user_id(uid, db=mock_db_session)
        assert result == uid

    @patch("shared_models.user_utils.get_or_create_canonical_user")
    async def test_email_with_db_resolve_failure(
        self, mock_get_or_create, mock_db_session
    ):
        """Email resolution failure should propagate."""
        mock_get_or_create.side_effect = RuntimeError("lookup failed")

        with pytest.raises(RuntimeError, match="lookup failed"):
            await resolve_canonical_user_id("fail@example.com", db=mock_db_session)

    @patch("shared_models.user_utils.get_or_create_canonical_user")
    async def test_email_with_integration_type(
        self, mock_get_or_create, mock_db_session
    ):
        """Email resolution should work with integration_type parameter."""
        expected_uid = str(uuid.uuid4())
        mock_get_or_create.return_value = expected_uid

        result = await resolve_canonical_user_id(
            "test@example.com",
            integration_type="WEB",
            db=mock_db_session,
        )
        assert result == expected_uid


class TestEnsureEmailMapping:
    """Tests for _ensure_email_mapping()."""

    async def test_creates_mapping_when_missing(self, mock_db_session):
        """_ensure_email_mapping creates a WEB mapping when none exists."""
        uid = str(uuid.uuid4())

        # No existing mapping
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        await _ensure_email_mapping(uid, "user@example.com", mock_db_session)

        # Should have executed the select and then the upsert
        assert mock_db_session.execute.call_count == 2
        mock_db_session.commit.assert_called_once()

    async def test_skips_when_mapping_exists(self, mock_db_session):
        """_ensure_email_mapping does nothing when mapping already exists."""
        uid = str(uuid.uuid4())

        mock_existing = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_existing
        mock_db_session.execute = AsyncMock(return_value=mock_result)

        await _ensure_email_mapping(uid, "user@example.com", mock_db_session)

        # Only the select query, no insert
        mock_db_session.execute.assert_called_once()
        mock_db_session.commit.assert_not_called()


class TestGetOrCreateCanonicalUser:
    """Tests for get_or_create_canonical_user()."""

    async def test_finds_existing_user_via_mapping(self, mock_db_session):
        """Should return user_id from existing mapping."""
        uid = str(uuid.uuid4())

        mock_mapping = MagicMock()
        mock_mapping.user_id = uid

        mock_result_mapping = MagicMock()
        mock_result_mapping.scalar_one_or_none.return_value = mock_mapping

        # _ensure_email_mapping also calls execute
        mock_ensure_result = MagicMock()
        mock_ensure_result.scalar_one_or_none.return_value = MagicMock()

        mock_db_session.execute = AsyncMock(
            side_effect=[mock_result_mapping, mock_ensure_result]
        )

        result = await get_or_create_canonical_user("user@example.com", mock_db_session)
        assert result == uid

    async def test_finds_existing_user_via_user_table(self, mock_db_session):
        """Should find user via User table when mapping doesn't exist."""
        uid = str(uuid.uuid4())

        # First query (mapping): not found
        mock_result_no_mapping = MagicMock()
        mock_result_no_mapping.scalar_one_or_none.return_value = None

        # Second query (User table): found
        mock_user = MagicMock()
        mock_user.user_id = uid
        mock_result_user = MagicMock()
        mock_result_user.scalar_one_or_none.return_value = mock_user

        # _ensure_email_mapping queries
        mock_ensure_result = MagicMock()
        mock_ensure_result.scalar_one_or_none.return_value = MagicMock()

        mock_db_session.execute = AsyncMock(
            side_effect=[mock_result_no_mapping, mock_result_user, mock_ensure_result]
        )

        result = await get_or_create_canonical_user("user@example.com", mock_db_session)
        assert result == uid

    async def test_creates_new_user(self, mock_db_session):
        """Should create a new user when neither mapping nor user exists."""
        # No mapping found
        mock_result_no_mapping = MagicMock()
        mock_result_no_mapping.scalar_one_or_none.return_value = None

        # No User found
        mock_result_no_user = MagicMock()
        mock_result_no_user.scalar_one_or_none.return_value = None

        # _ensure_email_mapping: no existing mapping
        mock_ensure_no_mapping = MagicMock()
        mock_ensure_no_mapping.scalar_one_or_none.return_value = None

        # _ensure_email_mapping: upsert succeeds
        mock_ensure_upsert = MagicMock()

        mock_db_session.execute = AsyncMock(
            side_effect=[
                mock_result_no_mapping,
                mock_result_no_user,
                mock_ensure_no_mapping,
                mock_ensure_upsert,
            ]
        )

        result = await get_or_create_canonical_user(
            "newuser@example.com", mock_db_session
        )

        # Result should be a valid UUID
        assert is_uuid(result)
        mock_db_session.add.assert_called_once()
        mock_db_session.flush.assert_called_once()

    async def test_handles_race_condition_duplicate(self, mock_db_session):
        """Should retry lookup on unique constraint violation during creation."""
        uid = str(uuid.uuid4())

        # No mapping found
        mock_result_no_mapping = MagicMock()
        mock_result_no_mapping.scalar_one_or_none.return_value = None

        # No User found on first lookup
        mock_result_no_user = MagicMock()
        mock_result_no_user.scalar_one_or_none.return_value = None

        # flush raises unique constraint violation
        mock_db_session.flush = AsyncMock(
            side_effect=Exception("duplicate key unique constraint")
        )

        # Retry lookup finds the user
        mock_user = MagicMock()
        mock_user.user_id = uid
        mock_result_found = MagicMock()
        mock_result_found.scalar_one_or_none.return_value = mock_user

        # _ensure_email_mapping: mapping exists
        mock_ensure_result = MagicMock()
        mock_ensure_result.scalar_one_or_none.return_value = MagicMock()

        mock_db_session.execute = AsyncMock(
            side_effect=[
                mock_result_no_mapping,
                mock_result_no_user,
                mock_result_found,  # retry lookup
                mock_ensure_result,  # _ensure_email_mapping select
            ]
        )

        result = await get_or_create_canonical_user("race@example.com", mock_db_session)
        assert result == uid

    async def test_non_constraint_error_reraises(self, mock_db_session):
        """Non-constraint errors should propagate."""
        # No mapping
        mock_result_no_mapping = MagicMock()
        mock_result_no_mapping.scalar_one_or_none.return_value = None

        # No user
        mock_result_no_user = MagicMock()
        mock_result_no_user.scalar_one_or_none.return_value = None

        # flush raises non-constraint error
        mock_db_session.flush = AsyncMock(side_effect=RuntimeError("unexpected error"))

        mock_db_session.execute = AsyncMock(
            side_effect=[mock_result_no_mapping, mock_result_no_user]
        )

        with pytest.raises(RuntimeError, match="unexpected error"):
            await get_or_create_canonical_user("bad@example.com", mock_db_session)
