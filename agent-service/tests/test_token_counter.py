"""Tests for agent_service.token_counter."""

import time

import pytest
from agent_service.token_counter import TokenCounter, TokenStats, TokenUsage


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the TokenCounter singleton before each test."""
    TokenCounter._instance = None
    yield
    TokenCounter._instance = None


class TestTokenUsage:
    """Tests for the TokenUsage dataclass."""

    def test_creation(self):
        usage = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
        assert usage.input_tokens == 10
        assert usage.output_tokens == 5
        assert usage.total_tokens == 15

    def test_auto_timestamp(self):
        before = time.time()
        usage = TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3)
        after = time.time()
        assert usage.timestamp is not None
        assert before <= usage.timestamp <= after

    def test_explicit_timestamp(self):
        usage = TokenUsage(
            input_tokens=1, output_tokens=2, total_tokens=3, timestamp=1234.0
        )
        assert usage.timestamp == 1234.0

    def test_optional_fields(self):
        usage = TokenUsage(
            input_tokens=5,
            output_tokens=10,
            total_tokens=15,
            model="gpt-4",
            context="test-ctx",
        )
        assert usage.model == "gpt-4"
        assert usage.context == "test-ctx"


class TestTokenStats:
    """Tests for the TokenStats dataclass."""

    def test_add_usage_accumulates(self):
        stats = TokenStats()
        stats.add_usage(TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15))
        stats.add_usage(TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30))

        assert stats.total_input_tokens == 30
        assert stats.total_output_tokens == 15
        assert stats.total_tokens == 45
        assert stats.call_count == 2
        assert len(stats.calls) == 2

    def test_add_usage_tracks_maximums(self):
        stats = TokenStats()
        stats.add_usage(TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15))
        stats.add_usage(TokenUsage(input_tokens=20, output_tokens=3, total_tokens=23))
        stats.add_usage(TokenUsage(input_tokens=5, output_tokens=15, total_tokens=20))

        assert stats.max_input_tokens == 20
        assert stats.max_output_tokens == 15
        assert stats.max_total_tokens == 23

    def test_initial_values(self):
        stats = TokenStats()
        assert stats.total_input_tokens == 0
        assert stats.total_output_tokens == 0
        assert stats.total_tokens == 0
        assert stats.call_count == 0
        assert stats.calls == []


class TestTokenCounter:
    """Tests for the TokenCounter singleton."""

    def test_singleton_pattern(self):
        c1 = TokenCounter()
        c2 = TokenCounter()
        assert c1 is c2

    def test_add_tokens(self):
        counter = TokenCounter()
        counter.add_tokens(input_tokens=10, output_tokens=5)
        assert counter._stats.total_input_tokens == 10
        assert counter._stats.total_output_tokens == 5
        assert counter._stats.total_tokens == 15
        assert counter._stats.call_count == 1

    def test_add_tokens_with_context(self):
        counter = TokenCounter()
        counter.add_tokens(input_tokens=10, output_tokens=5, context="agent-a")
        counter.add_tokens(input_tokens=20, output_tokens=10, context="agent-a")
        counter.add_tokens(input_tokens=5, output_tokens=3, context="agent-b")

        assert "agent-a" in counter._context_stats
        assert "agent-b" in counter._context_stats
        assert counter._context_stats["agent-a"].total_tokens == 45
        assert counter._context_stats["agent-b"].total_tokens == 8
        # Global stats should still accumulate
        assert counter._stats.total_tokens == 53

    def test_add_tokens_thread_safety(self):
        """Ensure add_tokens uses the lock (basic check)."""
        import threading

        counter = TokenCounter()
        errors = []

        def add_many():
            try:
                for _ in range(100):
                    counter.add_tokens(input_tokens=1, output_tokens=1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_many) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert counter._stats.call_count == 500
        assert counter._stats.total_tokens == 1000

    def test_add_tokens_with_model(self):
        counter = TokenCounter()
        counter.add_tokens(input_tokens=10, output_tokens=5, model="gpt-4")
        assert counter._stats.calls[0].model == "gpt-4"
