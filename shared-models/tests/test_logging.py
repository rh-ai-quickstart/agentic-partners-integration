"""Tests for shared_models.logging module."""

import logging

from shared_models.logging import LoggingConfig, configure_logging


class TestConfigureLogging:
    """Tests for configure_logging() convenience function."""

    def test_returns_logger(self):
        logger = configure_logging("test-service")
        assert logger is not None

    def test_logger_has_info_method(self):
        logger = configure_logging("test-service")
        assert hasattr(logger, "info")

    def test_logger_has_error_method(self):
        logger = configure_logging("test-service")
        assert hasattr(logger, "error")

    def test_logger_has_debug_method(self):
        logger = configure_logging("test-service")
        assert hasattr(logger, "debug")

    def test_logger_has_warning_method(self):
        logger = configure_logging("test-service")
        assert hasattr(logger, "warning")


class TestLoggingConfig:
    """Tests for LoggingConfig class."""

    def test_default_service_name(self):
        config = LoggingConfig()
        assert config.service_name == "unknown"

    def test_custom_service_name(self):
        config = LoggingConfig(service_name="my-service")
        assert config.service_name == "my-service"

    def test_get_log_level_default(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        config = LoggingConfig()
        assert config.log_level == logging.INFO

    def test_get_log_level_debug(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        config = LoggingConfig()
        assert config.log_level == logging.DEBUG

    def test_get_log_level_warning(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        config = LoggingConfig()
        assert config.log_level == logging.WARNING

    def test_get_log_level_error(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        config = LoggingConfig()
        assert config.log_level == logging.ERROR

    def test_get_log_level_critical(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "CRITICAL")
        config = LoggingConfig()
        assert config.log_level == logging.CRITICAL

    def test_get_log_level_invalid_falls_back_to_info(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "INVALID")
        config = LoggingConfig()
        assert config.log_level == logging.INFO

    def test_enable_debug_flag(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        config = LoggingConfig()
        assert config.enable_debug is True

    def test_enable_debug_flag_false(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "INFO")
        config = LoggingConfig()
        assert config.enable_debug is False


class TestAddServiceContext:
    """Tests for LoggingConfig._add_service_context."""

    def test_adds_service_name(self):
        config = LoggingConfig(service_name="test-svc")
        event_dict = {"event": "test message"}
        result = config._add_service_context(None, "info", event_dict)
        assert result["service"] == "test-svc"

    def test_preserves_existing_keys(self):
        config = LoggingConfig(service_name="test-svc")
        event_dict = {"event": "test message", "extra_key": "value"}
        result = config._add_service_context(None, "info", event_dict)
        assert result["extra_key"] == "value"
        assert result["service"] == "test-svc"
