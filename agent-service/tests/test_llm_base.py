"""Tests for agent_service.llm.base."""

import pytest
from unittest.mock import AsyncMock

from agent_service.llm.base import (
    BaseLLMClient,
    InstrumentedLLMClient,
    LLMMessage,
    LLMResponse,
)


class TestLLMMessage:
    """Tests for LLMMessage."""

    def test_creation(self):
        msg = LLMMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_to_dict(self):
        msg = LLMMessage(role="assistant", content="Hi there")
        d = msg.to_dict()
        assert d == {"role": "assistant", "content": "Hi there"}

    def test_system_role(self):
        msg = LLMMessage(role="system", content="You are helpful")
        assert msg.to_dict() == {"role": "system", "content": "You are helpful"}


class TestLLMResponse:
    """Tests for LLMResponse."""

    def test_creation(self):
        resp = LLMResponse(
            content="Hello!",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        assert resp.content == "Hello!"
        assert resp.model is None
        assert resp.finish_reason is None
        assert resp.latency_ms is None

    def test_total_tokens_property(self):
        resp = LLMResponse(
            content="test",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        assert resp.total_tokens == 15

    def test_total_tokens_missing_key(self):
        resp = LLMResponse(content="test", usage={})
        assert resp.total_tokens == 0

    def test_all_fields(self):
        resp = LLMResponse(
            content="data",
            usage={"total_tokens": 20},
            model="gpt-4",
            finish_reason="stop",
            latency_ms=123.4,
        )
        assert resp.model == "gpt-4"
        assert resp.finish_reason == "stop"
        assert resp.latency_ms == 123.4


class TestBaseLLMClient:
    """Tests for BaseLLMClient abstract methods."""

    async def test_create_completion_abstract_pass(self):
        """Line 68: covers the 'pass' in abstract create_completion."""

        class ConcreteClient(BaseLLMClient):
            async def create_completion(self, messages, temperature=0.7, max_tokens=None, **kwargs):
                return await super().create_completion(messages, temperature, max_tokens, **kwargs)

            def get_model_name(self):
                return "test"

        client = ConcreteClient()
        result = await client.create_completion([LLMMessage(role="user", content="Hi")])
        assert result is None

    def test_get_model_name_abstract_pass(self):
        """Line 77: covers the 'pass' in abstract get_model_name."""

        class ConcreteClient(BaseLLMClient):
            async def create_completion(self, messages, temperature=0.7, max_tokens=None, **kwargs):
                pass

            def get_model_name(self):
                return super().get_model_name()

        client = ConcreteClient()
        result = client.get_model_name()
        assert result is None


class TestInstrumentedLLMClient:
    """Tests for InstrumentedLLMClient wrapper."""

    async def test_wraps_calls_and_measures_latency(self, mock_llm_client):
        instrumented = InstrumentedLLMClient(mock_llm_client)
        messages = [LLMMessage(role="user", content="Hi")]

        result = await instrumented.create_completion(messages, temperature=0.5)

        mock_llm_client.create_completion.assert_awaited_once_with(
            messages, temperature=0.5, max_tokens=None,
        )
        assert result.content == "Test response"
        assert result.latency_ms is not None
        assert result.latency_ms >= 0

    async def test_get_model_name_delegates(self, mock_llm_client):
        instrumented = InstrumentedLLMClient(mock_llm_client)
        assert instrumented.get_model_name() == "test-model"

    async def test_handles_errors_and_still_raises(self, mock_llm_client):
        mock_llm_client.create_completion.side_effect = RuntimeError("LLM down")
        instrumented = InstrumentedLLMClient(mock_llm_client)
        messages = [LLMMessage(role="user", content="Hi")]

        with pytest.raises(RuntimeError, match="LLM down"):
            await instrumented.create_completion(messages)

    async def test_latency_measurement_on_success(self, mock_llm_client):
        """Latency should be a positive number on success."""
        instrumented = InstrumentedLLMClient(mock_llm_client)
        messages = [LLMMessage(role="user", content="test")]
        result = await instrumented.create_completion(messages)
        assert isinstance(result.latency_ms, float)
        assert result.latency_ms >= 0
