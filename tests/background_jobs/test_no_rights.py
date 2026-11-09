"""Unit tests for no-rights grace period jobs."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.background_jobs.no_rights import leave_no_rights_groups


@pytest.fixture(autouse=True)
def _no_protected_channels_by_default():
    """#145: the grace job now reads the protected-channel set once per run.

    Default it to EMPTY so every pre-existing test keeps asserting exactly what
    it asserted before — a non-protected group past grace still leaves. That
    also makes the positive control implicit: if the guard ever disabled the
    leave leg outright, these tests would fail. A test that exercises the
    protected branch patches this itself, inside its own `with` block.
    """
    with (
        patch(
            "app.background_jobs.no_rights.get_protected_channel_ids",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.background_jobs.no_rights.get_discussion_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        yield


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
            created_at=datetime.now(UTC),
            last_updated=datetime.now(UTC),
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
            created_at=datetime.now(UTC),
            last_updated=datetime.now(UTC),
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
            created_at=datetime.now(UTC),
            last_updated=datetime.now(UTC),
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


# --- #144: the leave leg is split by membership class ------------------------
#
# The job used to answer one class with one action. A group where the bot is
# still an ADMINISTRATOR with a right withheld receives every update and is one
# checkbox away from repair, but `perform_complete_group_cleanup` deleted its
# `approved_members` and `group_administrators` irreversibly. Measured
# 2026-11-09: two of five flagged groups were that class, one of them paying
# (96 credits, 2 approved members).


def _bot_user():
    from aiogram import types

    return types.User(id=999, is_bot=True, first_name="bot")


def _real_administrator(*, can_delete: bool, can_restrict: bool):
    """A real aiogram administrator, so the isinstance branch is the production one."""
    from aiogram import types

    return types.ChatMemberAdministrator(
        user=_bot_user(),
        can_be_edited=False,
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=can_delete,
        can_manage_video_chats=True,
        can_restrict_members=can_restrict,
        can_promote_members=False,
        can_change_info=True,
        can_invite_users=True,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("can_delete", "can_restrict", "missing"),
    [
        (True, False, "can_restrict_members"),
        (False, True, "can_delete_messages"),
        (False, False, "can_delete_messages, can_restrict_members"),
    ],
)
async def test_degraded_administrator_is_held_and_never_left(
    can_delete, can_restrict, missing, caplog
):
    """Close condition 1: administrator-with-a-right-withheld stays, flag kept."""
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
        ) as mock_cleanup,
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ) as mock_clear,
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [-1002496127723]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="Degraded Group", username=None)
        )
        mock_bot.get_chat_member = AsyncMock(
            return_value=_real_administrator(
                can_delete=can_delete, can_restrict=can_restrict
            )
        )

        with caplog.at_level(logging.INFO, logger="app.background_jobs.no_rights"):
            await leave_no_rights_groups()

        # The irreversible half must not run, and the clock must keep running so
        # the group is not silently dropped from the flow.
        mock_cleanup.assert_not_called()
        mock_clear.assert_not_called()

    assert any(
        "Holding group" in r.getMessage() and missing in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]


@pytest.mark.asyncio
async def test_plain_member_still_leaves():
    """Close condition 2: the class that genuinely cannot act is still evicted."""
    from aiogram import types

    with (
        patch("app.background_jobs.no_rights.load_config") as mock_load,
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace"
        ) as mock_get,
        patch("app.background_jobs.no_rights.bot") as mock_bot,
        patch(
            "app.background_jobs.no_rights.get_group",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_cleanup,
        patch("app.background_jobs.no_rights.send_admin_dm", new_callable=AsyncMock),
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [-1003078404548]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="Member Group", username=None)
        )
        mock_bot.get_chat_member = AsyncMock(
            return_value=types.ChatMemberMember(user=_bot_user())
        )

        await leave_no_rights_groups()

        mock_cleanup.assert_called_once_with(
            -1003078404548,
            "Member Group",
            None,
            status="paused",
            reason="no_rights_past_grace",
        )


@pytest.mark.asyncio
async def test_kicked_bot_still_leaves():
    """Close condition 2: `kicked` is unrecoverable in place — still leaves."""
    from aiogram import types

    with (
        patch("app.background_jobs.no_rights.load_config") as mock_load,
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace"
        ) as mock_get,
        patch("app.background_jobs.no_rights.bot") as mock_bot,
        patch(
            "app.background_jobs.no_rights.get_group",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_cleanup,
        patch("app.background_jobs.no_rights.send_admin_dm", new_callable=AsyncMock),
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [-1009999999999]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="Kicked Group", username=None)
        )
        mock_bot.get_chat_member = AsyncMock(
            return_value=types.ChatMemberBanned(
                user=_bot_user(), until_date=0, can_be_edited=False
            )
        )

        await leave_no_rights_groups()

        mock_cleanup.assert_called_once()


# --- #144 close condition 3: the detection DM, one shot ----------------------


@pytest.mark.asyncio
async def test_detection_dm_names_the_missing_right():
    """ "You removed Delete messages" is actionable; "we lost rights" is not."""
    from app.background_jobs.no_rights import notify_degraded_admin_rights

    with (
        patch(
            "app.background_jobs.no_rights.get_group", new_callable=AsyncMock
        ) as mock_get_group,
        patch(
            "app.background_jobs.no_rights.get_admin",
            new_callable=AsyncMock,
            return_value=MagicMock(is_active=True, language_code="en"),
        ),
        patch(
            "app.background_jobs.no_rights.send_admin_dm",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_send,
    ):
        from app.database.models import Group

        mock_get_group.return_value = Group(
            group_id=-1002496127723,
            admin_ids=[5272438221],
            moderation_enabled=True,
            member_ids=[],
            created_at=datetime.now(UTC),
            last_updated=datetime.now(UTC),
        )

        sent = await notify_degraded_admin_rights(
            [
                {
                    "group_id": -1002496127723,
                    "title": "блатные чупани",
                    "missing": "Delete messages and Ban users",
                }
            ]
        )

    assert sent == 1
    body = mock_send.call_args.args[1]
    assert "Delete messages and Ban users" in body
    assert "блатные чупани" in body
    # It must say the bot is STAYING: this class is not being evicted.
    assert "stay in the group" in body


@pytest.mark.asyncio
async def test_detection_dm_is_silent_when_nothing_was_detected():
    """No transition, no customer message — the list is the trigger."""
    from app.background_jobs.no_rights import notify_degraded_admin_rights

    with patch(
        "app.background_jobs.no_rights.send_admin_dm", new_callable=AsyncMock
    ) as mock_send:
        assert await notify_degraded_admin_rights([]) == 0

    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_detection_dm_skips_inactive_admins():
    """A deactivated admin is not a recipient, however the roster reads."""
    from app.background_jobs.no_rights import notify_degraded_admin_rights

    with (
        patch(
            "app.background_jobs.no_rights.get_group", new_callable=AsyncMock
        ) as mock_get_group,
        patch(
            "app.background_jobs.no_rights.get_admin",
            new_callable=AsyncMock,
            return_value=MagicMock(is_active=False, language_code="en"),
        ),
        patch(
            "app.background_jobs.no_rights.send_admin_dm", new_callable=AsyncMock
        ) as mock_send,
    ):
        from app.database.models import Group

        mock_get_group.return_value = Group(
            group_id=-1002496127723,
            admin_ids=[5272438221],
            moderation_enabled=True,
            member_ids=[],
            created_at=datetime.now(UTC),
            last_updated=datetime.now(UTC),
        )
        assert await notify_degraded_admin_rights(
            [{"group_id": -1002496127723, "title": "g", "missing": "Ban users"}]
        ) == 0

    mock_send.assert_not_called()