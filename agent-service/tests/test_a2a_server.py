"""Tests for agent_service.a2a.server."""

from unittest.mock import MagicMock, patch

import pytest


class TestA2AServer:
    """Tests for the dynamic A2A server builder."""

    @patch("agent_service.a2a.server.Starlette")
    @patch("agent_service.a2a.server.create_jsonrpc_routes")
    @patch("agent_service.a2a.server.create_agent_card_routes")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.create_agent_card")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_get_a2a_app_software(
        self,
        mock_executor_cls,
        mock_card_fn,
        mock_store_cls,
        mock_handler_cls,
        mock_card_routes,
        mock_jsonrpc_routes,
        mock_starlette_cls,
        monkeypatch,
    ):
        from agent_service.a2a.server import get_a2a_app

        monkeypatch.setenv(
            "SOFTWARE_SUPPORT_A2A_URL",
            "http://localhost:8080/a2a/software-support/",
        )

        config = {"name": "software-support", "departments": ["software"]}
        mock_card_routes.return_value = []
        mock_jsonrpc_routes.return_value = []

        result = get_a2a_app("software-support", config)

        mock_card_fn.assert_called_once_with(
            "software-support",
            config,
            "http://localhost:8080/a2a/software-support/",
        )
        mock_executor_cls.assert_called_once_with("software-support")
        mock_starlette_cls.assert_called_once()
        assert result is mock_starlette_cls.return_value

    @patch("agent_service.a2a.server.Starlette")
    @patch("agent_service.a2a.server.create_jsonrpc_routes")
    @patch("agent_service.a2a.server.create_agent_card_routes")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.create_agent_card")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_get_a2a_app_network(
        self,
        mock_executor_cls,
        mock_card_fn,
        mock_store_cls,
        mock_handler_cls,
        mock_card_routes,
        mock_jsonrpc_routes,
        mock_starlette_cls,
        monkeypatch,
    ):
        from agent_service.a2a.server import get_a2a_app

        monkeypatch.setenv(
            "NETWORK_SUPPORT_A2A_URL",
            "http://localhost:8080/a2a/network-support/",
        )

        config = {"name": "network-support", "departments": ["network"]}
        mock_card_routes.return_value = []
        mock_jsonrpc_routes.return_value = []

        result = get_a2a_app("network-support", config)

        mock_card_fn.assert_called_once_with(
            "network-support",
            config,
            "http://localhost:8080/a2a/network-support/",
        )
        mock_executor_cls.assert_called_once_with("network-support")
        assert result is mock_starlette_cls.return_value

    @patch("agent_service.a2a.server.Starlette")
    @patch("agent_service.a2a.server.create_jsonrpc_routes")
    @patch("agent_service.a2a.server.create_agent_card_routes")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.create_agent_card")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_build_a2a_app_creates_handler_and_executor(
        self,
        mock_executor_cls,
        mock_card_fn,
        mock_store_cls,
        mock_handler_cls,
        mock_card_routes,
        mock_jsonrpc_routes,
        mock_starlette_cls,
    ):
        from agent_service.a2a.server import _build_a2a_app

        config = {"name": "software-support", "departments": ["software"]}
        mock_card_routes.return_value = []
        mock_jsonrpc_routes.return_value = []

        result = _build_a2a_app("software-support", config, "http://localhost:8080/")

        mock_executor_cls.assert_called_once_with("software-support")
        mock_handler_cls.assert_called_once()
        mock_card_routes.assert_called_once()
        mock_jsonrpc_routes.assert_called_once()
        assert result is mock_starlette_cls.return_value

    @patch("agent_service.a2a.server.Starlette")
    @patch("agent_service.a2a.server.create_jsonrpc_routes")
    @patch("agent_service.a2a.server.create_agent_card_routes")
    @patch("agent_service.a2a.server.DefaultRequestHandler")
    @patch("agent_service.a2a.server.InMemoryTaskStore")
    @patch("agent_service.a2a.server.create_agent_card")
    @patch("agent_service.a2a.server.SpecialistAgentExecutor")
    def test_get_a2a_app_kubernetes(
        self,
        mock_executor_cls,
        mock_card_fn,
        mock_store_cls,
        mock_handler_cls,
        mock_card_routes,
        mock_jsonrpc_routes,
        mock_starlette_cls,
        monkeypatch,
    ):
        from agent_service.a2a.server import get_a2a_app

        monkeypatch.setenv(
            "KUBERNETES_SUPPORT_A2A_URL",
            "http://localhost:8080/a2a/kubernetes-support/",
        )

        config = {"name": "kubernetes-support", "departments": ["kubernetes"]}
        mock_card_routes.return_value = []
        mock_jsonrpc_routes.return_value = []

        result = get_a2a_app("kubernetes-support", config)

        mock_card_fn.assert_called_once_with(
            "kubernetes-support",
            config,
            "http://localhost:8080/a2a/kubernetes-support/",
        )
        mock_executor_cls.assert_called_once_with("kubernetes-support")
        assert result is mock_starlette_cls.return_value

    def test_get_a2a_app_default_url(self, monkeypatch):
        """When no env var is set, the default URL uses the agent name."""
        from agent_service.a2a.server import get_a2a_app

        monkeypatch.delenv("DB_SUPPORT_A2A_URL", raising=False)

        config = {"name": "db-support", "departments": ["database"]}

        with (
            patch("agent_service.a2a.server.create_agent_card") as mock_card,
            patch("agent_service.a2a.server.SpecialistAgentExecutor"),
            patch("agent_service.a2a.server.DefaultRequestHandler"),
            patch("agent_service.a2a.server.InMemoryTaskStore"),
            patch("agent_service.a2a.server.create_agent_card_routes", return_value=[]),
            patch("agent_service.a2a.server.create_jsonrpc_routes", return_value=[]),
            patch("agent_service.a2a.server.Starlette"),
        ):
            get_a2a_app("db-support", config)

            mock_card.assert_called_once_with(
                "db-support", config, "http://localhost:8080/a2a/db-support/"
            )
