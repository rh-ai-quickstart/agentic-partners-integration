"""Tests for shared_models.session_token_service module."""

from unittest.mock import AsyncMock, MagicMock

from shared_models.session_token_service import SessionTokenService


class TestGetTokenCounts:
    """Tests for SessionTokenService.get_token_counts()."""

    async def test_returns_correct_dict_structure(self, mock_db_session):
        mock_row = (100, 200, 300, 5, 50, 80, 130)
        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_db_session.execute.return_value = mock_result

        counts = await SessionTokenService.get_token_counts(mock_db_session, "sess-123")

        assert counts is not None
        assert counts["total_input_tokens"] == 100
        assert counts["total_output_tokens"] == 200
        assert counts["total_tokens"] == 300
        assert counts["llm_call_count"] == 5
        assert counts["max_input_tokens"] == 50
        assert counts["max_output_tokens"] == 80
        assert counts["max_total_tokens"] == 130

    async def test_returns_none_when_session_not_found(self, mock_db_session):
        mock_result = MagicMock()
        mock_result.first.return_value = None
        mock_db_session.execute.return_value = mock_result

        counts = await SessionTokenService.get_token_counts(
            mock_db_session, "nonexistent"
        )

        assert counts is None

    async def test_handles_none_values_in_row(self, mock_db_session):
        """None values in the row should be converted to 0."""
        mock_row = (None, None, None, None, None, None, None)
        mock_result = MagicMock()
        mock_result.first.return_value = mock_row
        mock_db_session.execute.return_value = mock_result

        counts = await SessionTokenService.get_token_counts(mock_db_session, "sess-123")

        assert counts is not None
        assert all(v == 0 for v in counts.values())

    async def test_handles_exception(self, mock_db_session):
        mock_db_session.execute.side_effect = Exception("db error")

        counts = await SessionTokenService.get_token_counts(mock_db_session, "sess-123")

        assert counts is None
