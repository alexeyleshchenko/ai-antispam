"""Unit tests for the unified scheduled jobs runner (metadata heal wiring)."""

import pytest
from unittest.mock import AsyncMock, patch

from app.background_jobs.scheduled_tasks import run_scheduled_jobs


@pytest.mark.asyncio
async def test_run_scheduled_jobs_calls_heal_bare_group_rows():
    """The daily job runner invokes the bare-row metadata heal."""
    with (
        patch(
            "app.background_jobs.scheduled_tasks.run_low_balance_checks",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.leave_no_rights_groups",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.cleanup_old_lookup_entries",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.cleanup_old_message_history",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.cleanup_pending_spam_examples",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.heal_bare_group_rows",
            new_callable=AsyncMock,
        ) as mock_heal,
    ):
        await run_scheduled_jobs()

    mock_heal.assert_awaited_once()
