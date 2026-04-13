"""Tests for agent_service.a2a.server."""

from unittest.mock import MagicMock, patch

import pytest


class TestA2AServer:
    """Tests for the A2A server builder functions."""

    @patch("agent_service.a2a.server.A2AStarletteApplication")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.create_software_support_card")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_get_software_support_a2a_app(
        self,
        mock_executor_cls,
        mock_card_fn,
        mock_store_cls,
        mock_handler_cls,
        mock_a2a_app_cls,
        monkeypatch,
    ):
        from agent_service.a2a.server import get_software_support_a2a_app

        monkeypatch.setenv(
            "SOFTWARE_SUPPORT_A2A_URL",
            "http://localhost:8080/a2a/software-support/",
        )

        mock_starlette_app = MagicMock()
        mock_a2a_app_cls.return_value.build.return_value = mock_starlette_app

        result = get_software_support_a2a_app()

        mock_card_fn.assert_called_once_with(
            "http://localhost:8080/a2a/software-support/"
        )
        mock_executor_cls.assert_called_once_with("software-support")
        assert result is mock_starlette_app

    @patch("agent_service.a2a.server.A2AStarletteApplication")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.create_network_support_card")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_get_network_support_a2a_app(
        self,
        mock_executor_cls,
        mock_card_fn,
        mock_store_cls,
        mock_handler_cls,
        mock_a2a_app_cls,
        monkeypatch,
    ):
        from agent_service.a2a.server import get_network_support_a2a_app

        monkeypatch.setenv(
            "NETWORK_SUPPORT_A2A_URL",
            "http://localhost:8080/a2a/network-support/",
        )

        mock_starlette_app = MagicMock()
        mock_a2a_app_cls.return_value.build.return_value = mock_starlette_app

        result = get_network_support_a2a_app()

        mock_card_fn.assert_called_once_with(
            "http://localhost:8080/a2a/network-support/"
        )
        mock_executor_cls.assert_called_once_with("network-support")
        assert result is mock_starlette_app

    @patch("agent_service.a2a.server.A2AStarletteApplication")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_build_a2a_app_unknown_agent_raises(
        self,
        mock_executor_cls,
        mock_store_cls,
        mock_handler_cls,
        mock_a2a_app_cls,
    ):
        from agent_service.a2a.server import _build_a2a_app

        with pytest.raises(ValueError, match="Unknown agent"):
            _build_a2a_app("unknown-agent", "http://localhost:8080/")

    @patch("agent_service.a2a.server.A2AStarletteApplication")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.create_software_support_card")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_build_a2a_app_creates_handler_and_executor(
        self,
        mock_executor_cls,
        mock_card_fn,
        mock_store_cls,
        mock_handler_cls,
        mock_a2a_app_cls,
    ):
        from agent_service.a2a.server import _build_a2a_app

        mock_starlette_app = MagicMock()
        mock_a2a_app_cls.return_value.build.return_value = mock_starlette_app

        result = _build_a2a_app("software-support", "http://localhost:8080/")

        # Verify executor was created with correct agent name
        mock_executor_cls.assert_called_once_with("software-support")
        # Verify handler was created with executor and task store
        mock_handler_cls.assert_called_once()
        # Verify A2A app was created with card and handler
        mock_a2a_app_cls.assert_called_once()
        assert result is mock_starlette_app
