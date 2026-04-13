"""Tests for shared_models.database module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared_models.database import (
    DatabaseConfig,
    DatabaseManager,
    get_db_session,
    get_db_session_dependency,
)


class TestDatabaseConfigWithDatabaseUrl:
    """Tests for DatabaseConfig when DATABASE_URL is set."""

    def test_parses_database_url(self, monkeypatch):
        monkeypatch.setenv(
            "DATABASE_URL",
            "postgresql://myuser:mypass@myhost:5433/mydb",
        )
        config = DatabaseConfig()
        assert config.host == "myhost"
        assert config.port == 5433
        assert config.database == "mydb"
        assert config.user == "myuser"
        assert config.password == "mypass"

    def test_parses_database_url_defaults(self, monkeypatch):
        """URL without explicit port/user/password should use fallbacks."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")
        config = DatabaseConfig()
        assert config.host == "localhost"
        assert config.database == "testdb"


class TestDatabaseConfigWithIndividualVars:
    """Tests for DatabaseConfig with individual POSTGRES_* env vars."""

    def test_uses_individual_env_vars(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "custom-host")
        monkeypatch.setenv("POSTGRES_PORT", "5555")
        monkeypatch.setenv("POSTGRES_DB", "custom_db")
        monkeypatch.setenv("POSTGRES_USER", "custom_user")
        monkeypatch.setenv("POSTGRES_PASSWORD", "custom_pass")

        config = DatabaseConfig()
        assert config.host == "custom-host"
        assert config.port == 5555
        assert config.database == "custom_db"
        assert config.user == "custom_user"
        assert config.password == "custom_pass"

    def test_defaults_without_env_vars(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_HOST", raising=False)
        monkeypatch.delenv("POSTGRES_PORT", raising=False)
        monkeypatch.delenv("POSTGRES_DB", raising=False)
        monkeypatch.delenv("POSTGRES_USER", raising=False)
        monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

        config = DatabaseConfig()
        assert config.host == "pgvector"
        assert config.port == 5432
        assert config.database == "llama_agents"
        assert config.user == "pgvector"
        assert config.password == "pgvector"


class TestParseDatabaseUrl:
    """Tests for DatabaseConfig._parse_database_url()."""

    def test_full_url_parsing(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("POSTGRES_HOST", raising=False)
        config = DatabaseConfig.__new__(DatabaseConfig)
        config._parse_database_url(
            "postgresql://admin:secret@db.example.com:5434/production"
        )
        assert config.host == "db.example.com"
        assert config.port == 5434
        assert config.database == "production"
        assert config.user == "admin"
        assert config.password == "secret"


class TestConnectionStringProperties:
    """Tests for connection_string and sync_connection_string properties."""

    def test_connection_string(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "testhost")
        monkeypatch.setenv("POSTGRES_PORT", "5432")
        monkeypatch.setenv("POSTGRES_DB", "testdb")
        monkeypatch.setenv("POSTGRES_USER", "testuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "testpass")

        config = DatabaseConfig()
        assert config.connection_string == (
            "postgresql+asyncpg://testuser:testpass@testhost:5432/testdb"
        )

    def test_sync_connection_string(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "testhost")
        monkeypatch.setenv("POSTGRES_PORT", "5432")
        monkeypatch.setenv("POSTGRES_DB", "testdb")
        monkeypatch.setenv("POSTGRES_USER", "testuser")
        monkeypatch.setenv("POSTGRES_PASSWORD", "testpass")

        config = DatabaseConfig()
        assert config.sync_connection_string == (
            "postgresql+psycopg://testuser:testpass@testhost:5432/testdb"
        )


class TestValidate:
    """Tests for DatabaseConfig.validate()."""

    def test_validate_complete_config(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "host")
        monkeypatch.setenv("POSTGRES_DB", "db")
        monkeypatch.setenv("POSTGRES_USER", "user")
        monkeypatch.setenv("POSTGRES_PASSWORD", "pass")

        config = DatabaseConfig()
        assert config.validate() is True

    def test_validate_incomplete_config(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "host")
        monkeypatch.setenv("POSTGRES_DB", "db")
        monkeypatch.setenv("POSTGRES_USER", "user")

        config = DatabaseConfig()
        # Host, database, and user are all set, so validate passes
        # even without password since password isn't in required_fields
        assert config.validate() is True

    def test_validate_missing_host(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        config = DatabaseConfig.__new__(DatabaseConfig)
        config.host = ""
        config.database = "db"
        config.user = "user"
        config.password = "pass"
        assert config.validate() is False


class TestGetAlembicConfig:
    """Tests for DatabaseConfig.get_alembic_config()."""

    def test_returns_dict_with_expected_keys(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("POSTGRES_HOST", "host")
        monkeypatch.setenv("POSTGRES_PORT", "5432")
        monkeypatch.setenv("POSTGRES_DB", "db")
        monkeypatch.setenv("POSTGRES_USER", "user")
        monkeypatch.setenv("POSTGRES_PASSWORD", "pass")

        config = DatabaseConfig()
        alembic = config.get_alembic_config()

        assert "sqlalchemy.url" in alembic
        assert "sqlalchemy.echo" in alembic
        assert alembic["sqlalchemy.url"] == config.sync_connection_string
        assert alembic["sqlalchemy.echo"] in ("true", "false")


class TestGetDatabaseManager:
    """Tests for get_database_manager() singleton."""

    def test_returns_singleton(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)

        import shared_models.database as db_module

        # Reset global singleton
        db_module._db_manager = None

        try:
            manager1 = db_module.get_database_manager()
            manager2 = db_module.get_database_manager()
            assert manager1 is manager2
        finally:
            # Clean up global state
            db_module._db_manager = None


class TestDatabaseManagerLogDatabaseConfig:
    """Tests for DatabaseManager.log_database_config()."""

    async def test_log_database_config_success(self, monkeypatch):
        """log_database_config should execute SELECT 1 and log success."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)
        manager.config = DatabaseConfig.__new__(DatabaseConfig)
        manager.config.host = "testhost"
        manager.config.database = "testdb"
        manager.config.pool_size = 5
        manager.config.max_overflow = 10
        manager.config.pool_timeout = 30
        manager.config.pool_recycle = 3600

        mock_engine = MagicMock()
        mock_pool = MagicMock()
        mock_pool.__class__.__name__ = "AsyncAdaptedQueuePool"
        mock_engine.pool = mock_pool

        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_conn.execute = AsyncMock(return_value=mock_result)

        # mock engine.begin() as async context manager
        mock_begin_ctx = AsyncMock()
        mock_begin_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_begin_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_begin_ctx)

        manager.engine = mock_engine

        await manager.log_database_config()

        mock_conn.execute.assert_called_once()

    async def test_log_database_config_failure_raises(self, monkeypatch):
        """log_database_config should re-raise on connection failure."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)
        manager.config = DatabaseConfig.__new__(DatabaseConfig)
        manager.config.host = "testhost"
        manager.config.database = "testdb"
        manager.config.pool_size = 5
        manager.config.max_overflow = 10
        manager.config.pool_timeout = 30
        manager.config.pool_recycle = 3600

        mock_engine = MagicMock()
        mock_pool = MagicMock()
        mock_pool.__class__.__name__ = "AsyncAdaptedQueuePool"
        mock_engine.pool = mock_pool

        mock_begin_ctx = AsyncMock()
        mock_begin_ctx.__aenter__ = AsyncMock(
            side_effect=ConnectionError("cannot connect")
        )
        mock_begin_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine.begin = MagicMock(return_value=mock_begin_ctx)

        manager.engine = mock_engine

        with pytest.raises(ConnectionError, match="cannot connect"):
            await manager.log_database_config()


class TestDatabaseManagerClose:
    """Tests for DatabaseManager.close()."""

    async def test_close_disposes_engine(self, monkeypatch):
        """close() should call engine.dispose()."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)
        mock_engine = AsyncMock()
        mock_engine.dispose = AsyncMock()
        manager.engine = mock_engine

        await manager.close()

        mock_engine.dispose.assert_called_once()


class TestDatabaseManagerGetSession:
    """Tests for DatabaseManager.get_session() context manager."""

    async def test_get_session_yields_session(self, monkeypatch):
        """get_session should yield a session from async_session."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)

        mock_session = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        manager.async_session = MagicMock(return_value=mock_session_ctx)

        async with manager.get_session() as session:
            assert session is mock_session

        mock_session.close.assert_called_once()

    async def test_get_session_rollback_on_error(self, monkeypatch):
        """get_session should rollback on exception."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)

        mock_session = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        manager.async_session = MagicMock(return_value=mock_session_ctx)

        with pytest.raises(ValueError, match="test error"):
            async with manager.get_session() as session:
                raise ValueError("test error")

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()


class TestDatabaseManagerHealthCheck:
    """Tests for DatabaseManager.health_check()."""

    async def test_health_check_success(self, monkeypatch):
        """health_check returns True when database is reachable."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock()
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        manager.async_session = MagicMock(return_value=mock_session_ctx)

        result = await manager.health_check()
        assert result is True

    async def test_health_check_failure(self, monkeypatch):
        """health_check returns False when database is unreachable."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=ConnectionError("db down")
        )
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        manager.async_session = MagicMock(return_value=mock_session_ctx)

        result = await manager.health_check()
        assert result is False


class TestDatabaseManagerWaitForMigration:
    """Tests for DatabaseManager.wait_for_migration()."""

    async def test_wait_for_migration_success_any_version(self, monkeypatch):
        """wait_for_migration returns True when migration is found."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("EXPECTED_MIGRATION_VERSION", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)

        mock_session = AsyncMock()
        mock_version_result = MagicMock()
        mock_version_row = MagicMock()
        mock_version_row.__getitem__ = MagicMock(return_value="abc123")
        mock_version_result.fetchone.return_value = mock_version_row

        # All queries succeed
        mock_session.execute = AsyncMock(return_value=mock_version_result)
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        manager.async_session = MagicMock(return_value=mock_session_ctx)

        result = await manager.wait_for_migration(timeout=10)
        assert result is True

    async def test_wait_for_migration_success_expected_version(self, monkeypatch):
        """wait_for_migration returns True when expected version is found."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("EXPECTED_MIGRATION_VERSION", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)

        mock_session = AsyncMock()
        mock_version_result = MagicMock()
        mock_version_row = MagicMock()
        mock_version_row.__getitem__ = MagicMock(return_value="v2")
        mock_version_result.fetchone.return_value = mock_version_row
        mock_session.execute = AsyncMock(return_value=mock_version_result)
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        manager.async_session = MagicMock(return_value=mock_session_ctx)

        result = await manager.wait_for_migration(
            expected_version="v2", timeout=10
        )
        assert result is True

    async def test_wait_for_migration_timeout(self, monkeypatch):
        """wait_for_migration returns False on timeout."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("EXPECTED_MIGRATION_VERSION", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=Exception("table does not exist")
        )
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        manager.async_session = MagicMock(return_value=mock_session_ctx)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.wait_for_migration(timeout=0)

        assert result is False

    async def test_wait_for_migration_polls_until_ready(self, monkeypatch):
        """wait_for_migration polls when version row is not yet present."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("EXPECTED_MIGRATION_VERSION", raising=False)

        manager = DatabaseManager.__new__(DatabaseManager)

        mock_session = AsyncMock()

        # First call: no version row; second call: version row + tables
        no_version_result = MagicMock()
        no_version_result.fetchone.return_value = None

        version_result = MagicMock()
        version_row = MagicMock()
        version_row.__getitem__ = MagicMock(return_value="abc123")
        version_result.fetchone.return_value = version_row

        table_result = MagicMock()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return no_version_result
            return version_result

        mock_session.execute = AsyncMock(side_effect=execute_side_effect)
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        manager.async_session = MagicMock(return_value=mock_session_ctx)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await manager.wait_for_migration(timeout=30)

        assert result is True
        # Should have been called at least twice (once for no version, then again)
        assert call_count >= 2

    async def test_wait_for_migration_env_var(self, monkeypatch):
        """wait_for_migration reads EXPECTED_MIGRATION_VERSION from env."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("EXPECTED_MIGRATION_VERSION", "env_version")

        manager = DatabaseManager.__new__(DatabaseManager)

        mock_session = AsyncMock()
        mock_version_result = MagicMock()
        mock_version_row = MagicMock()
        mock_version_row.__getitem__ = MagicMock(return_value="env_version")
        mock_version_result.fetchone.return_value = mock_version_row
        mock_session.execute = AsyncMock(return_value=mock_version_result)
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
        manager.async_session = MagicMock(return_value=mock_session_ctx)

        result = await manager.wait_for_migration(timeout=10)
        assert result is True


class TestGetDbSession:
    """Tests for get_db_session() module-level context manager."""

    async def test_get_db_session_yields_session(self, monkeypatch):
        """get_db_session yields a session from the global database manager."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        import shared_models.database as db_module

        mock_session = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_manager = MagicMock()
        mock_get_session_ctx = AsyncMock()
        mock_get_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_manager.get_session = MagicMock(return_value=mock_get_session_ctx)

        with patch.object(db_module, "get_database_manager", return_value=mock_manager):
            async with get_db_session() as session:
                assert session is mock_session

        mock_session.close.assert_called_once()

    async def test_get_db_session_rollback_on_error(self, monkeypatch):
        """get_db_session rolls back and re-raises on error."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        import shared_models.database as db_module

        mock_session = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_get_session_ctx = AsyncMock()
        mock_get_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_manager = MagicMock()
        mock_manager.get_session = MagicMock(return_value=mock_get_session_ctx)

        with patch.object(db_module, "get_database_manager", return_value=mock_manager):
            with pytest.raises(RuntimeError, match="db error"):
                async with get_db_session() as session:
                    raise RuntimeError("db error")

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()


class TestGetDbSessionDependency:
    """Tests for get_db_session_dependency() async generator."""

    async def test_yields_session(self, monkeypatch):
        """get_db_session_dependency yields a session."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        import shared_models.database as db_module

        mock_session = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_get_session_ctx = AsyncMock()
        mock_get_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_manager = MagicMock()
        mock_manager.get_session = MagicMock(return_value=mock_get_session_ctx)

        with patch.object(db_module, "get_database_manager", return_value=mock_manager):
            gen = get_db_session_dependency()
            session = await gen.__anext__()
            assert session is mock_session
            # Signal generator to finalize
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

        mock_session.close.assert_called_once()

    async def test_rollback_on_error(self, monkeypatch):
        """get_db_session_dependency rolls back on error."""
        monkeypatch.delenv("DATABASE_URL", raising=False)

        import shared_models.database as db_module

        mock_session = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_get_session_ctx = AsyncMock()
        mock_get_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_manager = MagicMock()
        mock_manager.get_session = MagicMock(return_value=mock_get_session_ctx)

        with patch.object(db_module, "get_database_manager", return_value=mock_manager):
            gen = get_db_session_dependency()
            session = await gen.__anext__()
            # Throw an error into the generator
            with pytest.raises(RuntimeError, match="dependency error"):
                await gen.athrow(RuntimeError("dependency error"))

        mock_session.rollback.assert_called_once()
        mock_session.close.assert_called_once()
