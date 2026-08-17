"""Unit tests for no-rights grace period jobs."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.background_jobs.no_rights import leave_no_rights_groups


@pytest.mark.asyncio
async def test_leave_no_rights_groups_clears_flag_when_rights_restored():
    """When bot has rights, clear_no_rights_detected_at is called and no leave."""

    class FakeAdminWithRights:
        can_delete_messages = True
        can_restrict_members = True

    with (
        patch("app.background_jobs.no_rights.load_config") as mock_load,
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace"
        ) as mock_get,
        patch("app.background_jobs.no_rights.bot") as mock_bot,
        patch(
            "app.background_jobs.no_rights.ChatMemberAdministrator",
            FakeAdminWithRights,
        ),
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ) as mock_clear,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [100]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999))

        admin_member = FakeAdminWithRights()
        mock_bot.get_chat_member = AsyncMock(return_value=admin_member)

        await leave_no_rights_groups()

        mock_clear.assert_called_once_with(100)
        mock_cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_leave_no_rights_groups_leaves_when_no_rights():
    """When bot has no rights, perform_complete_group_cleanup is called."""
    with (
        patch("app.background_jobs.no_rights.load_config") as mock_load,
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace"
        ) as mock_get,
        patch("app.background_jobs.no_rights.bot") as mock_bot,
        patch(
            "app.background_jobs.no_rights.get_group", new_callable=AsyncMock
        ) as mock_get_group,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
        patch(
            "app.background_jobs.no_rights.send_admin_dm",
            new_callable=AsyncMock,
        ) as mock_send,
        patch(
            "app.background_jobs.no_rights.get_admin",
            new_callable=AsyncMock,
            return_value=MagicMock(is_active=True, language_code="en"),
        ),
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [100]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="Test Group", username="test")
        )

        member_mock = MagicMock()
        member_mock.can_delete_messages = False
        member_mock.can_restrict_members = False
        mock_bot.get_chat_member = AsyncMock(return_value=member_mock)

        from app.database.models import Group

        mock_get_group.return_value = Group(
            group_id=100,
            admin_ids=[111],
            moderation_enabled=True,
            member_ids=[],
            created_at=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

        mock_cleanup.return_value = True

        await leave_no_rights_groups()

        mock_cleanup.assert_called_once_with(
            100,
            "Test Group",
            "test",
            status="paused",
            reason="no_rights_past_grace",
        )
        mock_send.assert_called_once()
        assert "Test Group" in str(mock_send.call_args) or "100" in str(
            mock_send.call_args
        )


@pytest.mark.asyncio
async def test_leave_no_rights_groups_empty_list_returns_early():
    """When no groups past grace, returns without API calls."""
    with (
        patch("app.background_jobs.no_rights.load_config") as mock_load,
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace"
        ) as mock_get,
        patch("app.background_jobs.no_rights.bot") as mock_bot,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = []

        await leave_no_rights_groups()

        mock_cleanup.assert_not_called()
        mock_bot.me.assert_not_called()


@pytest.mark.asyncio
async def test_leave_no_rights_groups_cleans_stale_group_chat_not_found():
    """When rights check fails with chat not found, still run stale cleanup."""
    bad_request = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: chat not found"
    )

    with (
        patch("app.background_jobs.no_rights.load_config") as mock_load,
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace"
        ) as mock_get,
        patch("app.background_jobs.no_rights.bot") as mock_bot,
        patch(
            "app.background_jobs.no_rights.get_group", new_callable=AsyncMock
        ) as mock_get_group,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_cleanup,
        patch(
            "app.background_jobs.no_rights.send_admin_dm",
            new_callable=AsyncMock,
        ) as mock_send,
        patch(
            "app.background_jobs.no_rights.get_admin",
            new_callable=AsyncMock,
            return_value=MagicMock(is_active=True, language_code="en"),
        ),
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [-1001234567890]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="Stale Group", username=None)
        )
        mock_bot.get_chat_member = AsyncMock(side_effect=bad_request)

        from app.database.models import Group

        mock_get_group.return_value = Group(
            group_id=-1001234567890,
            admin_ids=[111],
            moderation_enabled=True,
            member_ids=[],
            created_at=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

        await leave_no_rights_groups()

        mock_cleanup.assert_called_once_with(
            -1001234567890,
            "Stale Group",
            None,
            status="paused",
            reason="no_rights_past_grace",
        )
        mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_leave_no_rights_groups_cleans_stale_group_bot_kicked():
    """When rights check fails because bot was kicked, still run stale cleanup."""
    forbidden = TelegramForbiddenError(
        method=MagicMock(),
        message="Forbidden: bot was kicked from the supergroup chat",
    )

    with (
        patch("app.background_jobs.no_rights.load_config") as mock_load,
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace"
        ) as mock_get,
        patch("app.background_jobs.no_rights.bot") as mock_bot,
        patch(
            "app.background_jobs.no_rights.get_group", new_callable=AsyncMock
        ) as mock_get_group,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_cleanup,
        patch(
            "app.background_jobs.no_rights.send_admin_dm",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.no_rights.get_admin",
            new_callable=AsyncMock,
            return_value=MagicMock(is_active=True, language_code="en"),
        ),
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [-1009876543210]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="Kicked Group", username=None)
        )
        mock_bot.get_chat_member = AsyncMock(side_effect=forbidden)

        from app.database.models import Group

        mock_get_group.return_value = Group(
            group_id=-1009876543210,
            admin_ids=[222],
            moderation_enabled=True,
            member_ids=[],
            created_at=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

        await leave_no_rights_groups()

        mock_cleanup.assert_called_once_with(
            -1009876543210,
            "Kicked Group",
            None,
            status="paused",
            reason="no_rights_past_grace",
        )


@pytest.mark.asyncio
async def test_leave_no_rights_groups_logs_title_username(caplog):
    """
    When get_chat resolves, the leave/cleanup log lines carry the group
    title and username (mirrors the low_balance pattern).
    """
    import logging

    with (
        patch("app.background_jobs.no_rights.load_config") as mock_load,
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace"
        ) as mock_get,
        patch("app.background_jobs.no_rights.bot") as mock_bot,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_cleanup,
        patch(
            "app.background_jobs.no_rights.get_group", new_callable=AsyncMock
        ) as mock_get_group,
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [-100123]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="Test Group", username="testgroup")
        )
        member_mock = MagicMock()
        member_mock.can_delete_messages = False
        member_mock.can_restrict_members = False
        mock_bot.get_chat_member = AsyncMock(return_value=member_mock)
        mock_get_group.return_value = None

        with caplog.at_level(logging.INFO, logger="app.background_jobs.no_rights"):
            await leave_no_rights_groups()

        mock_cleanup.assert_called_once_with(
            -100123,
            "Test Group",
            "testgroup",
            status="paused",
            reason="no_rights_past_grace",
        )

    assert any(
        "Left no-rights group" in r.getMessage()
        and "'Test Group' @testgroup" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_leave_no_rights_groups_falls_back_to_bare_id(caplog):
    """
    When get_chat raises (e.g. chat inaccessible), the log still fires with
    the bare chat ID — no crash, graceful fallback.
    """
    import logging

    with (
        patch("app.background_jobs.no_rights.load_config") as mock_load,
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace"
        ) as mock_get,
        patch("app.background_jobs.no_rights.bot") as mock_bot,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_cleanup,
        patch(
            "app.background_jobs.no_rights.get_group", new_callable=AsyncMock
        ) as mock_get_group,
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [-100123]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            side_effect=Exception("TelegramForbiddenError: bot is not a member")
        )
        member_mock = MagicMock()
        member_mock.can_delete_messages = False
        member_mock.can_restrict_members = False
        mock_bot.get_chat_member = AsyncMock(return_value=member_mock)
        mock_get_group.return_value = None

        with caplog.at_level(logging.INFO, logger="app.background_jobs.no_rights"):
            await leave_no_rights_groups()

        mock_cleanup.assert_called_once_with(
            -100123,
            None,
            None,
            status="paused",
            reason="no_rights_past_grace",
        )

    assert any(
        "Left no-rights group" in r.getMessage() and "-100123" in r.getMessage()
        for r in caplog.records
    )
