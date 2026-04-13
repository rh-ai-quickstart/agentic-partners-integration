"""Tests for shared_models.health module."""

from unittest.mock import AsyncMock

from shared_models.health import HealthCheckResult, HealthChecker, simple_health_check


class TestHealthCheckResult:
    """Tests for HealthCheckResult class."""

    def test_to_dict_structure(self):
        result = HealthCheckResult(
            status="healthy",
            service_name="test-svc",
            version="1.0.0",
            database_connected=True,
        )
        d = result.to_dict()
        assert d["status"] == "healthy"
        assert d["service"] == "test-svc"
        assert d["version"] == "1.0.0"
        assert d["database_connected"] is True
        assert "timestamp" in d
        assert isinstance(d["services"], dict)

    def test_to_dict_has_iso_timestamp(self):
        result = HealthCheckResult()
        d = result.to_dict()
        # ISO format should contain 'T' separator
        assert "T" in d["timestamp"]

    def test_default_values(self):
        result = HealthCheckResult()
        assert result.status == "healthy"
        assert result.service_name == "unknown"
        assert result.version == "0.1.0"
        assert result.database_connected is False
        assert result.services == {}


class TestHealthChecker:
    """Tests for HealthChecker class."""

    def test_init(self):
        checker = HealthChecker("my-service", "2.0.0")
        assert checker.service_name == "my-service"
        assert checker.version == "2.0.0"

    async def test_check_database_success(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)

        checker = HealthChecker("test-svc")
        result = await checker.check_database(mock_db)
        assert result is True
        mock_db.execute.assert_called_once()

    async def test_check_database_failure(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("connection refused"))

        checker = HealthChecker("test-svc")
        result = await checker.check_database(mock_db)
        assert result is False

    async def test_perform_health_check_with_db(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)

        checker = HealthChecker("test-svc", "1.0.0")
        result = await checker.perform_health_check(db=mock_db)

        assert result.status == "healthy"
        assert result.database_connected is True
        assert result.service_name == "test-svc"
        assert result.version == "1.0.0"

    async def test_perform_health_check_without_db(self):
        checker = HealthChecker("test-svc")
        result = await checker.perform_health_check()

        assert result.status == "degraded"
        assert result.database_connected is False

    async def test_perform_health_check_with_additional_checks(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)

        async def check_redis():
            return True

        async def check_cache():
            return False

        checker = HealthChecker("test-svc")
        result = await checker.perform_health_check(
            db=mock_db,
            additional_checks={"redis": check_redis, "cache": check_cache},
        )

        assert result.services["redis"] == "healthy"
        assert result.services["cache"] == "unhealthy"

    async def test_perform_health_check_additional_check_exception(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)

        async def failing_check():
            raise RuntimeError("boom")

        checker = HealthChecker("test-svc")
        result = await checker.perform_health_check(
            db=mock_db,
            additional_checks={"broken": failing_check},
        )

        assert "error:" in result.services["broken"]

    async def test_perform_health_check_db_failure_returns_degraded(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=Exception("db down"))

        checker = HealthChecker("test-svc")
        result = await checker.perform_health_check(db=mock_db)

        assert result.status == "degraded"
        assert result.database_connected is False


class TestSimpleHealthCheck:
    """Tests for simple_health_check() convenience function."""

    async def test_returns_dict(self):
        result = await simple_health_check("test-svc", "1.0.0")
        assert isinstance(result, dict)
        assert result["service"] == "test-svc"
        assert result["version"] == "1.0.0"

    async def test_with_db(self):
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=None)

        result = await simple_health_check("test-svc", "1.0.0", db=mock_db)
        assert result["database_connected"] is True

    async def test_without_db(self):
        result = await simple_health_check("test-svc")
        assert result["database_connected"] is False
        assert result["status"] == "degraded"
