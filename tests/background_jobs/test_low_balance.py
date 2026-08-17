"""Unit tests for low balance warning jobs."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.background_jobs.low_balance import (
    check_depletion_timeline,
    check_week_ahead_warnings,
    leave_sole_payer_groups,
    run_low_balance_checks,
)


@pytest.mark.asyncio
async def test_check_week_ahead_warnings_sends_and_marks():
    """Week-ahead warning is sent and low_balance_warned_at is set."""
    with (
        patch("app.background_jobs.low_balance.load_config") as mock_load,
        patch(
            "app.background_jobs.low_balance.get_admins_for_low_balance_warnings"
        ) as mock_get,
        patch("app.background_jobs.low_balance.get_admin") as mock_get_admin,
        patch(
            "app.background_jobs.low_balance.send_admin_dm",
            new_callable=AsyncMock,
        ) as mock_send,
        patch(
            "app.background_jobs.low_balance.mark_low_balance_warned",
            new_callable=AsyncMock,
        ) as mock_mark,
    ):
        mock_load.return_value = {
            "billing": {"low_balance_threshold": 50, "depletion_grace_days": 7},
        }
        mock_get.return_value = [
            {"admin_id": 111, "credits": 30, "spent_last_week": 40},
        ]
        mock_get_admin.return_value = type(
            "Admin", (), {"is_active": True, "language_code": "ru"}
        )()
        mock_send.return_value = True

        await check_week_ahead_warnings()

        mock_send.assert_called_once()
        assert "30" in mock_send.call_args[0][1]
        mock_mark.assert_called_once_with(111)


@pytest.mark.asyncio
async def test_check_week_ahead_skips_inactive_admin():
    """Inactive admins are skipped."""
    with (
        patch("app.background_jobs.low_balance.load_config") as mock_load,
        patch(
            "app.background_jobs.low_balance.get_admins_for_low_balance_warnings"
        ) as mock_get,
        patch("app.background_jobs.low_balance.get_admin") as mock_get_admin,
        patch(
            "app.background_jobs.low_balance.send_admin_dm",
            new_callable=AsyncMock,
        ) as mock_send,
        patch(
            "app.background_jobs.low_balance.mark_low_balance_warned",
            new_callable=AsyncMock,
        ) as mock_mark,
    ):
        mock_load.return_value = {"billing": {"low_balance_threshold": 50}}
        mock_get.return_value = [
            {"admin_id": 111, "credits": 30, "spent_last_week": 40}
        ]
        mock_get_admin.return_value = type(
            "Admin", (), {"is_active": False, "language_code": None}
        )()
        mock_send.return_value = True

        await check_week_ahead_warnings()

        mock_send.assert_not_called()
        mock_mark.assert_not_called()


@pytest.mark.asyncio
async def test_check_depletion_timeline_day7_leaves_groups():
    """Day 7: leave_sole_payer_groups and clear_depletion_flags called."""
    depleted_at = datetime.now(timezone.utc) - timedelta(days=8)
    with (
        patch("app.background_jobs.low_balance.load_config") as mock_load,
        patch(
            "app.background_jobs.low_balance.get_admins_for_depletion_timeline"
        ) as mock_get,
        patch("app.background_jobs.low_balance.get_admin") as mock_get_admin,
        patch(
            "app.background_jobs.low_balance.leave_sole_payer_groups",
            new_callable=AsyncMock,
        ) as mock_leave,
        patch(
            "app.background_jobs.low_balance.clear_depletion_flags",
            new_callable=AsyncMock,
        ) as mock_clear,
    ):
        mock_load.return_value = {
            "billing": {
                "depletion_grace_days": 7,
                "warn_day_after": True,
                "warn_day_before": True,
            },
        }
        mock_get.return_value = [
            {"admin_id": 222, "credits_depleted_at": depleted_at},
        ]
        mock_get_admin.return_value = type(
            "Admin", (), {"is_active": True, "language_code": "ru"}
        )()

        await check_depletion_timeline()

        mock_leave.assert_called_once_with(222)
        mock_clear.assert_called_once_with(222)


@pytest.mark.asyncio
async def test_leave_sole_payer_groups_leaves_and_notifies():
    """Leaves groups with no paying admins and notifies admin."""
    with (
        patch(
            "app.background_jobs.low_balance.get_admin_group_ids",
            new_callable=AsyncMock,
        ) as mock_get_groups,
        patch(
            "app.background_jobs.low_balance.get_paying_admins", new_callable=AsyncMock
        ) as mock_paying,
        patch("app.background_jobs.low_balance.load_config") as mock_load,
        patch(
            "app.background_jobs.low_balance.get_admin", new_callable=AsyncMock
        ) as mock_get_admin,
        patch("app.background_jobs.low_balance.bot") as mock_bot,
        patch(
            "app.background_jobs.low_balance.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
        patch(
            "app.background_jobs.low_balance.send_admin_dm",
            new_callable=AsyncMock,
        ) as mock_send,
    ):
        mock_get_groups.return_value = [100, 200]
        mock_paying.side_effect = [
            [],
            [333],
        ]  # group 100: no payers, group 200: admin 333 pays
        mock_load.return_value = {"system": {"project_website": "https://test.ru"}}
        mock_get_admin.return_value = type(
            "Admin", (), {"is_active": True, "language_code": "ru"}
        )()
        mock_bot.get_chat = AsyncMock(
            return_value=type("Chat", (), {"title": "Test", "username": "test"})()
        )
        mock_bot.me = AsyncMock(
            return_value=type("Bot", (), {"username": "test_bot"})()
        )
        mock_cleanup.return_value = True
        mock_send.return_value = True

        await leave_sole_payer_groups(111)

        assert mock_cleanup.call_count == 1
        mock_cleanup.assert_called_with(
            100,
            "Test",
            "test",
            status="paused",
            reason="low_balance_unpaid",
        )
        mock_send.assert_called_once()
        assert "100" in str(mock_send.call_args) or "Test" in str(mock_send.call_args)


@pytest.mark.asyncio
async def test_run_low_balance_checks_calls_both():
    """run_low_balance_checks runs both week-ahead and timeline."""
    with (
        patch(
            "app.background_jobs.low_balance.check_week_ahead_warnings",
            new_callable=AsyncMock,
        ) as mock_week,
        patch(
            "app.background_jobs.low_balance.check_depletion_timeline",
            new_callable=AsyncMock,
        ) as mock_timeline,
    ):
        await run_low_balance_checks()
        mock_week.assert_called_once()
        mock_timeline.assert_called_once()
