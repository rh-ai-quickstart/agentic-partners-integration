"""Tests for shared_models.aaa_service module."""

from unittest.mock import AsyncMock, MagicMock, patch

from shared_models.aaa_service import AAAService
from shared_models.models import UserRole


class TestGetUserByEmail:
    """Tests for AAAService.get_user_by_email()."""

    async def test_finds_user(self, mock_db_session, mock_user):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_user
        mock_db_session.execute.return_value = mock_result

        user = await AAAService.get_user_by_email(mock_db_session, "test@example.com")

        assert user is mock_user
        mock_db_session.execute.assert_called_once()

    async def test_returns_none_when_not_found(self, mock_db_session):
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db_session.execute.return_value = mock_result

        user = await AAAService.get_user_by_email(
            mock_db_session, "nonexistent@example.com"
        )

        assert user is None

    async def test_returns_none_on_exception(self, mock_db_session):
        mock_db_session.execute.side_effect = Exception("db error")

        user = await AAAService.get_user_by_email(mock_db_session, "test@example.com")

        assert user is None


class TestGetOrCreateUser:
    """Tests for AAAService.get_or_create_user()."""

    @patch.object(AAAService, "get_user_by_email")
    async def test_returns_existing_user(self, mock_get, mock_db_session, mock_user):
        mock_get.return_value = mock_user

        user = await AAAService.get_or_create_user(mock_db_session, "test@example.com")

        assert user is mock_user
        mock_db_session.add.assert_not_called()

    @patch.object(AAAService, "get_user_by_email")
    async def test_creates_new_user(self, mock_get, mock_db_session):
        mock_get.return_value = None

        user = await AAAService.get_or_create_user(
            mock_db_session,
            "new@example.com",
            role=UserRole.ENGINEER,
            organization="Acme",
            department="Engineering",
            departments=["software"],
        )

        mock_db_session.add.assert_called_once()
        mock_db_session.commit.assert_called_once()
        mock_db_session.refresh.assert_called_once()
        # The returned user is the one that was added
        assert user is not None

    @patch.object(AAAService, "get_user_by_email")
    async def test_returns_none_on_exception(self, mock_get, mock_db_session):
        mock_get.return_value = None
        mock_db_session.commit.side_effect = Exception("db error")

        user = await AAAService.get_or_create_user(mock_db_session, "new@example.com")

        assert user is None
        mock_db_session.rollback.assert_called_once()


class TestGetUserDepartments:
    """Tests for AAAService.get_user_departments()."""

    @patch.object(AAAService, "get_user_by_email")
    async def test_returns_departments_from_db(
        self, mock_get, mock_db_session, mock_user
    ):
        mock_user.departments = ["software", "engineering"]
        mock_get.return_value = mock_user

        departments = await AAAService.get_user_departments(
            mock_db_session, "test@example.com"
        )

        assert departments == ["software", "engineering"]

    @patch("shared_models.aaa_service.get_user_departments_from_opa")
    @patch.object(AAAService, "get_user_by_email")
    async def test_falls_back_to_opa(
        self, mock_get, mock_opa, mock_db_session, mock_user
    ):
        mock_user.departments = []
        mock_get.return_value = mock_user
        mock_opa.return_value = ["hr", "finance"]

        departments = await AAAService.get_user_departments(
            mock_db_session, "test@example.com"
        )

        assert departments == ["hr", "finance"]
        mock_opa.assert_called_once_with("test@example.com")

    @patch("shared_models.aaa_service.get_user_departments_from_opa")
    @patch.object(AAAService, "get_user_by_email")
    async def test_falls_back_to_opa_when_user_not_found(
        self, mock_get, mock_opa, mock_db_session
    ):
        mock_get.return_value = None
        mock_opa.return_value = ["marketing"]

        departments = await AAAService.get_user_departments(
            mock_db_session, "unknown@example.com"
        )

        assert departments == ["marketing"]

    @patch.object(AAAService, "get_user_by_email")
    async def test_returns_empty_on_exception(self, mock_get, mock_db_session):
        mock_get.side_effect = Exception("db error")

        departments = await AAAService.get_user_departments(
            mock_db_session, "test@example.com"
        )

        assert departments == []


class TestUpdateUserPermissions:
    """Tests for AAAService.update_user_permissions()."""

    @patch.object(AAAService, "get_user_by_email")
    async def test_updates_role_and_departments(
        self, mock_get, mock_db_session, mock_user
    ):
        mock_get.return_value = mock_user

        result = await AAAService.update_user_permissions(
            mock_db_session,
            "test@example.com",
            role=UserRole.ADMIN,
            departments=["software", "hr"],
        )

        assert result is True
        assert mock_user.role == UserRole.ADMIN
        assert mock_user.departments == ["software", "hr"]
        mock_db_session.commit.assert_called_once()

    @patch.object(AAAService, "get_user_by_email")
    async def test_returns_false_for_nonexistent_user(self, mock_get, mock_db_session):
        mock_get.return_value = None

        result = await AAAService.update_user_permissions(
            mock_db_session,
            "unknown@example.com",
            role=UserRole.ADMIN,
        )

        assert result is False

    @patch.object(AAAService, "get_user_by_email")
    async def test_updates_privileges_and_status(
        self, mock_get, mock_db_session, mock_user
    ):
        mock_get.return_value = mock_user

        result = await AAAService.update_user_permissions(
            mock_db_session,
            "test@example.com",
            privileges={"can_delete": True},
            status="suspended",
        )

        assert result is True
        assert mock_user.privileges == {"can_delete": True}
        assert mock_user.status == "suspended"

    @patch.object(AAAService, "get_user_by_email")
    async def test_returns_false_on_exception(
        self, mock_get, mock_db_session, mock_user
    ):
        mock_get.return_value = mock_user
        mock_db_session.commit.side_effect = Exception("db error")

        result = await AAAService.update_user_permissions(
            mock_db_session,
            "test@example.com",
            role=UserRole.ADMIN,
        )

        assert result is False
        mock_db_session.rollback.assert_called_once()
