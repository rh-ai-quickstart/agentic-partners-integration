"""Tests for shared_models.fastapi_utils module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared_models.fastapi_utils import create_health_check_endpoint, create_shared_lifespan


class TestCreateHealthCheckEndpoint:
    """Tests for create_health_check_endpoint()."""

    async def test_returns_correct_structure(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)

        result = await create_health_check_endpoint(
            service_name="test-svc",
            version="1.0.0",
            db=mock_db,
        )

        assert isinstance(result, dict)
        assert result["service"] == "test-svc"
        assert result["version"] == "1.0.0"
        assert "status" in result
        assert "database_connected" in result
        assert "services" in result

    async def test_with_additional_checks(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)

        async def check_redis():
            return True

        result = await create_health_check_endpoint(
            service_name="test-svc",
            version="1.0.0",
            db=mock_db,
            additional_checks={"redis": check_redis},
        )

        assert result["services"]["redis"] == "healthy"

    async def test_with_custom_health_logic(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)

        async def custom_logic(db):
            return {"custom_metric": "ok"}

        result = await create_health_check_endpoint(
            service_name="test-svc",
            version="1.0.0",
            db=mock_db,
            custom_health_logic=custom_logic,
        )

        assert result["custom_metric"] == "ok"

    async def test_handles_custom_health_logic_error(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)

        async def failing_logic(db):
            raise RuntimeError("custom check failed")

        result = await create_health_check_endpoint(
            service_name="test-svc",
            version="1.0.0",
            db=mock_db,
            custom_health_logic=failing_logic,
        )

        # Should not crash; should include error info
        assert "status" in result
        assert result.get("custom_health") == "failed"

    async def test_handles_overall_error_gracefully(self):
        """If HealthChecker itself blows up, endpoint should return unhealthy."""
        mock_db = AsyncMock()

        with patch(
            "shared_models.fastapi_utils.HealthChecker"
        ) as mock_checker_cls:
            mock_checker = MagicMock()
            mock_checker.perform_health_check = AsyncMock(
                side_effect=RuntimeError("boom")
            )
            mock_checker_cls.return_value = mock_checker

            result = await create_health_check_endpoint(
                service_name="test-svc",
                version="1.0.0",
                db=mock_db,
            )

        assert result["status"] == "unhealthy"
        assert result["service"] == "test-svc"
        assert "error" in result

    async def test_db_failure_returns_degraded(self):
        """When database check fails, status should be degraded."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("db down"))

        result = await create_health_check_endpoint(
            service_name="test-svc",
            version="1.0.0",
            db=mock_db,
        )

        assert result["status"] == "degraded"
        assert result["database_connected"] is False


class TestCreateSharedLifespan:
    """Tests for create_shared_lifespan() context manager."""

    @patch("shared_models.fastapi_utils.get_database_manager")
    async def test_yields_during_lifespan(self, mock_get_db_manager):
        """create_shared_lifespan should yield control during its lifespan."""
        mock_manager = MagicMock()
        mock_manager.wait_for_migration = AsyncMock(return_value=True)
        mock_manager.log_database_config = AsyncMock()
        mock_manager.close = AsyncMock()
        mock_get_db_manager.return_value = mock_manager

        entered = False
        async with create_shared_lifespan("test-svc", "1.0.0"):
            entered = True

        assert entered is True
        mock_manager.wait_for_migration.assert_called_once_with(timeout=300)
        mock_manager.log_database_config.assert_called_once()
        mock_manager.close.assert_called_once()

    @patch("shared_models.fastapi_utils.get_database_manager")
    async def test_calls_custom_startup_and_shutdown(self, mock_get_db_manager):
        """create_shared_lifespan should call custom_startup and custom_shutdown."""
        mock_manager = MagicMock()
        mock_manager.wait_for_migration = AsyncMock(return_value=True)
        mock_manager.log_database_config = AsyncMock()
        mock_manager.close = AsyncMock()
        mock_get_db_manager.return_value = mock_manager

        startup_called = False
        shutdown_called = False

        async def custom_startup():
            nonlocal startup_called
            startup_called = True

        async def custom_shutdown():
            nonlocal shutdown_called
            shutdown_called = True

        async with create_shared_lifespan(
            "test-svc",
            "1.0.0",
            custom_startup=custom_startup,
            custom_shutdown=custom_shutdown,
        ):
            assert startup_called is True
            assert shutdown_called is False

        assert shutdown_called is True

    @patch("shared_models.fastapi_utils.get_database_manager")
    async def test_handles_migration_timeout(self, mock_get_db_manager):
        """create_shared_lifespan should raise when migration times out."""
        mock_manager = MagicMock()
        mock_manager.wait_for_migration = AsyncMock(return_value=False)
        mock_get_db_manager.return_value = mock_manager

        with pytest.raises(Exception, match="Database migration did not complete"):
            async with create_shared_lifespan(
                "test-svc", "1.0.0", migration_timeout=10
            ):
                pass  # pragma: no cover

        mock_manager.wait_for_migration.assert_called_once_with(timeout=10)

    @patch("shared_models.fastapi_utils.get_database_manager")
    async def test_handles_startup_failure(self, mock_get_db_manager):
        """create_shared_lifespan should raise when custom_startup fails."""
        mock_manager = MagicMock()
        mock_manager.wait_for_migration = AsyncMock(return_value=True)
        mock_manager.log_database_config = AsyncMock()
        mock_get_db_manager.return_value = mock_manager

        async def failing_startup():
            raise RuntimeError("startup boom")

        with pytest.raises(RuntimeError, match="startup boom"):
            async with create_shared_lifespan(
                "test-svc", "1.0.0", custom_startup=failing_startup
            ):
                pass  # pragma: no cover

    @patch("shared_models.fastapi_utils.get_database_manager")
    async def test_shutdown_error_does_not_propagate(self, mock_get_db_manager):
        """create_shared_lifespan should not propagate custom_shutdown errors."""
        mock_manager = MagicMock()
        mock_manager.wait_for_migration = AsyncMock(return_value=True)
        mock_manager.log_database_config = AsyncMock()
        mock_manager.close = AsyncMock()
        mock_get_db_manager.return_value = mock_manager

        async def failing_shutdown():
            raise RuntimeError("shutdown boom")

        # Should not raise even though custom_shutdown fails
        async with create_shared_lifespan(
            "test-svc", "1.0.0", custom_shutdown=failing_shutdown
        ):
            pass

        # close should still be called even after shutdown error
        mock_manager.close.assert_called_once()
