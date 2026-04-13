"""Unified database utilities for all services."""

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = structlog.get_logger()


class DatabaseConfig:
    """Database configuration class."""

    def __init__(self) -> None:
        # Check if DATABASE_URL is provided
        database_url = os.getenv("DATABASE_URL")

        if database_url:
            # Parse DATABASE_URL if provided
            self._parse_database_url(database_url)
        else:
            # Fall back to individual environment variables
            self.host = os.getenv("POSTGRES_HOST", "pgvector")
            self.port = int(os.getenv("POSTGRES_PORT", "5432"))
            self.database = os.getenv("POSTGRES_DB", "llama_agents")
            self.user = os.getenv("POSTGRES_USER", "pgvector")
            self.password = os.getenv("POSTGRES_PASSWORD", "pgvector")

        # Connection pool settings
        self.pool_size = int(os.getenv("DB_POOL_SIZE", "5"))
        self.max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "10"))
        self.pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "30"))
        self.pool_recycle = int(os.getenv("DB_POOL_RECYCLE", "3600"))

        # Debug settings
        self.echo_sql = os.getenv("SQL_DEBUG", "false").lower() == "true"

        # Connection string
        self._connection_string = self._build_connection_string()

    def _parse_database_url(self, database_url: str) -> None:
        """Parse DATABASE_URL into connection components."""
        from urllib.parse import urlparse

        parsed = urlparse(database_url)

        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 5432
        self.database = parsed.path.lstrip("/") if parsed.path else "postgres"
        self.user = parsed.username or "postgres"
        self.password = parsed.password or ""

    def _build_connection_string(self) -> str:
        """Build PostgreSQL connection string."""
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

    @property
    def connection_string(self) -> str:
        """Get the database connection string."""
        return self._connection_string

    @property
    def sync_connection_string(self) -> str:
        """Get the synchronous database connection string (for Alembic)."""
        return f"postgresql+psycopg://{self.user}:{self.password}@{self.host}:{self.port}/{self.database}"

    def validate(self) -> bool:
        """Validate database configuration."""
        required_fields = [self.host, self.database, self.user]

        if not all(required_fields):
            logger.error(
                "Database configuration incomplete",
                host=self.host,
                database=self.database,
                user=self.user,
                password_set=bool(self.password),
            )
            return False

        return True

    def get_alembic_config(self) -> dict[str, Any]:
        """Get configuration for Alembic."""
        return {
            "sqlalchemy.url": self.sync_connection_string,
            "sqlalchemy.echo": str(self.echo_sql).lower(),
        }


class DatabaseManager:
    """Database connection and session manager."""

    def __init__(self) -> None:
        self.config = DatabaseConfig()

        # Create async engine with connection pooling for better performance
        self.engine = create_async_engine(
            self.config.connection_string,
            echo=self.config.echo_sql,
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=self.config.pool_recycle,  # Recycle connections periodically
            pool_size=self.config.pool_size,
            max_overflow=self.config.max_overflow,
            pool_timeout=self.config.pool_timeout,
            connect_args={
                "command_timeout": 30,  # Connection timeout
                "server_settings": {
                    "application_name": "partner-agent",
                    "statement_timeout": "30000",  # 30 second statement timeout
                    "idle_in_transaction_session_timeout": "300000",  # 5 minute idle timeout
                },
            },
        )

        # Create session maker
        self.async_session = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def log_database_config(self) -> None:
        """Log database configuration and test connection at startup."""
        try:
            # Get the actual pool class being used
            pool_class = self.engine.pool.__class__.__name__

            # Test the connection
            async with self.engine.begin() as conn:
                result = await conn.execute(text("SELECT 1 as test"))
                test_value = result.scalar()

            logger.info(
                "Database configuration initialized successfully",
                pool_class=pool_class,
                pool_size=self.config.pool_size,
                max_overflow=self.config.max_overflow,
                pool_timeout=self.config.pool_timeout,
                pool_recycle=self.config.pool_recycle,
                connection_test=test_value,
                host=self.config.host,
                database=self.config.database,
                application_name="partner-agent",
            )

        except Exception as e:
            logger.error(
                "Failed to initialize database connection",
                error=str(e),
                pool_class=pool_class if "pool_class" in locals() else "unknown",
                pool_size=self.config.pool_size,
                max_overflow=self.config.max_overflow,
                host=self.config.host,
                database=self.config.database,
            )
            raise

    async def close(self) -> None:
        """Close database connections."""
        await self.engine.dispose()
        logger.info("Database connections closed")

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get a database session."""
        async with self.async_session() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def health_check(self) -> bool:
        """Check database connectivity."""
        try:
            async with self.get_session() as session:
                await session.execute(text("SELECT 1"))
                return True
        except Exception as e:
            logger.error("Database health check failed", error=str(e))
            return False

    async def wait_for_migration(
        self, expected_version: str | None = None, timeout: int = 300
    ) -> bool:
        """Wait for database migration to complete.

        If expected_version is provided (or set via EXPECTED_MIGRATION_VERSION env var),
        waits for that specific version. Otherwise, waits for any migration to be present
        in the alembic_version table and verifies core tables exist.
        """
        import asyncio
        from time import time

        # Allow pinning to a specific version via env var, but don't require it
        if expected_version is None:
            expected_version = os.getenv("EXPECTED_MIGRATION_VERSION")

        start_time = time()
        logger.info(
            "Waiting for database migration to complete",
            expected_version=expected_version or "any",
        )

        while (time() - start_time) < timeout:
            try:
                async with self.get_session() as session:
                    if expected_version:
                        # Check for a specific version
                        result = await session.execute(
                            text(
                                "SELECT version_num FROM alembic_version WHERE version_num = :version"
                            ),
                            {"version": expected_version},
                        )
                    else:
                        # Check that any migration has run
                        result = await session.execute(
                            text("SELECT version_num FROM alembic_version LIMIT 1")
                        )

                    version_row = result.fetchone()

                    if not version_row:
                        logger.debug(
                            "Migration not ready",
                            expected=expected_version or "any",
                        )
                        await asyncio.sleep(5)
                        continue

                    current_version = version_row[0]

                    # Verify that core tables exist and are accessible
                    await session.execute(
                        text("SELECT 1 FROM request_sessions LIMIT 1")
                    )
                    await session.execute(text("SELECT 1 FROM request_logs LIMIT 1"))
                    await session.execute(
                        text("SELECT 1 FROM user_integration_configs LIMIT 1")
                    )

                    logger.info(
                        "Database migration completed successfully",
                        version=current_version,
                        elapsed_seconds=int(time() - start_time),
                    )
                    return True

            except Exception as e:
                logger.debug(
                    "Migration not ready yet",
                    expected_version=expected_version or "any",
                    error=str(e),
                )
                await asyncio.sleep(5)

        logger.error(
            "Timeout waiting for database migration",
            expected_version=expected_version or "any",
            timeout=timeout,
        )
        return False


class DatabaseUtils:
    """Shared database utility functions."""

    pass


# Global database manager instance
_db_manager = None


def get_database_manager() -> DatabaseManager:
    """Get the global database manager instance."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def get_db_config() -> DatabaseConfig:
    """Get the global database configuration."""
    return get_database_manager().config


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database sessions with automatic cleanup."""
    db_manager = get_database_manager()
    async with db_manager.get_session() as session:
        try:
            yield session
        except Exception as e:
            logger.error("Database session error", error=str(e))
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_db_session_dependency() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions."""
    db_manager = get_database_manager()
    async with db_manager.get_session() as session:
        try:
            yield session
        except Exception as e:
            logger.error("Database session error", error=str(e))
            await session.rollback()
            raise
        finally:
            await session.close()


# Alias for backwards compatibility
get_db = get_db_session_dependency
