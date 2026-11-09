"""#145: the no-rights grace job must never leave a protected channel.

A channel in `get_protected_channel_ids()` is the linked channel of a discussion
group the bot protects. Leaving it cascades into losing that group — the hazard
`handle_channel_post` already refuses (Valeri trace / #34) and that the grace
job, written before that guard, bypassed.

The three cases below are the issue's close conditions 1-3. Condition 4 is a
live run on 2026-11-16 and cannot be asserted here.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from app.background_jobs import no_rights
from app.background_jobs.no_rights import leave_no_rights_groups

SV_CLUB = -1002107631154
SV_CHAT = -1002021099836
PLAIN_GROUP = -1003078404548


def _bot_user() -> types.User:
    return types.User(id=999, is_bot=True, first_name="bot")


@pytest.mark.asyncio
async def test_protected_channel_is_never_left():
    """Close condition 1: a protected channel past grace is not passed to the
    leave path at all — and its status transition still happens."""
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
        ) as mock_leave,
        patch(
            "app.background_jobs.no_rights.pause_group_preserving_data",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_pause,
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ) as mock_clear,
        patch(
            "app.background_jobs.no_rights.get_protected_channel_ids",
            new_callable=AsyncMock,
            return_value=[SV_CLUB],
        ),
        patch(
            "app.background_jobs.no_rights.get_discussion_group_id",
            new_callable=AsyncMock,
            return_value=SV_CHAT,
        ),
        patch("app.background_jobs.no_rights.send_admin_dm", new_callable=AsyncMock),
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [SV_CLUB]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="SV CLUB", username=None)
        )
        # The real channel answers 400 to both calls; the guard must never even
        # ask, because a protected channel does not leave whatever the answer.
        mock_bot.get_chat_member = AsyncMock(
            side_effect=AssertionError("rights probed for a protected channel")
        )

        await leave_no_rights_groups()

        mock_leave.assert_not_called()
        # The destructive sibling is not even imported here, so the guard
        # cannot reach it: `cleanup_group_data` hard-deletes
        # group_administrators and approved_members.
        assert not hasattr(no_rights, "cleanup_group_data")
        mock_pause.assert_called_once_with(
            SV_CLUB, reason="no_rights_protected_channel"
        )
        mock_clear.assert_called_once_with(SV_CLUB)


@pytest.mark.asyncio
async def test_protected_channel_clock_cleared_so_a_second_run_does_nothing():
    """Close condition 2: clearing the clock is what stops re-enqueueing.

    The second run is given an EMPTY candidate list, which is what the grace
    predicate returns once `no_rights_detected_at` is NULL and `status` is no
    longer `active` — the two writes the guard makes.
    """
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
        ) as mock_leave,
        patch(
            "app.background_jobs.no_rights.pause_group_preserving_data",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_pause,
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.no_rights.get_protected_channel_ids",
            new_callable=AsyncMock,
            return_value=[SV_CLUB],
        ),
        patch(
            "app.background_jobs.no_rights.get_discussion_group_id",
            new_callable=AsyncMock,
            return_value=SV_CHAT,
        ),
        patch("app.background_jobs.no_rights.send_admin_dm", new_callable=AsyncMock),
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.side_effect = [[SV_CLUB], []]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="SV CLUB", username=None)
        )

        await leave_no_rights_groups()
        assert mock_pause.call_count == 1

        await leave_no_rights_groups()
        assert mock_pause.call_count == 1, "second run re-enqueued the channel"
        mock_leave.assert_not_called()


@pytest.mark.asyncio
async def test_non_protected_group_still_leaves_beside_a_protected_one():
    """Close condition 3, the positive control: the guard must not disable the
    leave leg. Same run, one protected id and one not — only the plain group
    leaves, and the protected one is never handed to the leave path."""
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
        ) as mock_leave,
        patch(
            "app.background_jobs.no_rights.pause_group_preserving_data",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_pause,
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.no_rights.get_protected_channel_ids",
            new_callable=AsyncMock,
            return_value=[SV_CLUB],
        ),
        patch(
            "app.background_jobs.no_rights.get_discussion_group_id",
            new_callable=AsyncMock,
            return_value=SV_CHAT,
        ),
        patch("app.background_jobs.no_rights.send_admin_dm", new_callable=AsyncMock),
    ):
        mock_load.return_value = {"billing": {"no_rights_grace_days": 7}}
        mock_get.return_value = [SV_CLUB, PLAIN_GROUP]
        mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
        mock_bot.get_chat = AsyncMock(
            return_value=MagicMock(title="A Group", username=None)
        )
        mock_bot.get_chat_member = AsyncMock(
            return_value=types.ChatMemberMember(user=_bot_user())
        )

        await leave_no_rights_groups()

        mock_leave.assert_called_once_with(
            PLAIN_GROUP,
            "A Group",
            None,
            status="paused",
            reason="no_rights_past_grace",
        )
        # The protected channel went through the status-only path instead.
        mock_pause.assert_called_once_with(
            SV_CLUB, reason="no_rights_protected_channel"
        )
