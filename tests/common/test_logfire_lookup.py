"""Unit tests for logfire_lookup module (get_weekly_stats only).

Message lookup functions have been removed; context is now stored in PostgreSQL.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.app.common.logfire_lookup import get_weekly_stats


class TestGetWeeklyStats:
    """Tests for get_weekly_stats."""

    @pytest.mark.asyncio
    async def test_empty_chat_ids_returns_empty(self):
        """When chat_ids is empty, return empty dict."""
        result = await get_weekly_stats([])
        assert result == {}

    @pytest.mark.asyncio
    async def test_returns_zero_stats_by_default(self):
        """When no data found, return zeros for each chat."""
        mock_client = MagicMock()
        mock_client.query_json_rows.return_value = {"rows": []}

        with patch(
            "src.app.common.logfire_lookup._get_client", return_value=mock_client
        ):
            result = await get_weekly_stats([1001, 1002])

        assert result == {
            1001: {"processed": 0, "spam": 0},
            1002: {"processed": 0, "spam": 0},
        }
