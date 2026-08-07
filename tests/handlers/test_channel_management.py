"""Tests for channel management decision flow (protect discussion vs leave)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramForbiddenError

from src.app.handlers.message.channel_management import (
    build_channel_discussion_added_message,
    build_channel_instruction_message,
    build_channel_instruction_userbot_message,
    notify_channel_admins_and_leave,
)


def _make_chat(title: str = "Test Channel", username: str | None = None) -> MagicMock:
    chat = MagicMock()
    chat.id = -1001297263491
    chat.title = title
    chat.username = username
    chat.linked_chat_id = None
    return chat


def _make_bot(*, leave_side_effect=None) -> AsyncMock:
    bot = AsyncMock()
    bot.id = 7574715711
    bot.get_chat_administrators = AsyncMock(return_value=[])
    if leave_side_effect is not None:
        bot.leave_chat = AsyncMock(side_effect=leave_side_effect)
    else:
        bot.leave_chat = AsyncMock()
    return bot


class TestBuildChannelInstructionUserbotMessage:
    """Test userbot message builder includes preamble for unknown sender."""

    def test_includes_preamble_identifying_bot(self):
        """Preamble identifies the official bot and explains why from this account."""
        msg = build_channel_instruction_userbot_message(
            "Test Channel", None, "testchannel"
        )
        assert "@ai_antispam_blocker_bot" in msg or "@ai_spam_blocker_bot" in msg
        assert "Message from the" in msg or "team" in msg
        assert "couldn't" in msg or "couldn" in msg or "could not" in msg

    def test_includes_instruction_body(self):
        """Instruction body is same as standard message."""
        body = build_channel_instruction_message(
            "Test", "https://t.me/discuss", "testchan"
        )
        userbot_msg = build_channel_instruction_userbot_message(
            "Test", "https://t.me/discuss", "testchan"
        )
        assert body in userbot_msg
        assert "Discussion Group" in userbot_msg or "discuss" in userbot_msg


class TestBuildChannelDiscussionAddedMessage:
    """Test the protect-mode message builder (channel.discussion_added)."""

    def test_renders_with_discussion_link(self):
        """Protect message includes channel, discussion, and link when username known."""
        msg = build_channel_discussion_added_message(
            "My Channel", "https://t.me/discuss", "My Discussion", "mychannel"
        )
        assert "My Channel" in msg
        assert "My Discussion" in msg
        assert "https://t.me/discuss" in msg
        # Uses the discussion_added key, not wrong_place
        assert "instead of the discussion group" not in msg

    def test_renders_without_link(self):
        """Protect message renders when discussion has no public username."""
        msg = build_channel_discussion_added_message(
            "My Channel", None, "Private Discussion"
        )
        assert "My Channel" in msg
        assert "Private Discussion" in msg
        assert "go to group" not in msg


class TestDecisionFlowLeaveBranch:
    """No linked discussion group (or not a member) → wrong_place + leave."""

    @pytest.mark.asyncio
    async def test_no_discussion_group_leaves(self):
        """Channel without a discussion group → wrong_place instruction + leave."""
        chat = _make_chat()
        bot = _make_bot()

        with (
            patch(
                "src.app.handlers.message.channel_management._resolve_linked_discussion_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.app.handlers.message.channel_management.get_discussion_username",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.app.handlers.message.channel_management.notify_channel_admins",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_notify,
        ):
            await notify_channel_admins_and_leave(chat, bot)

            bot.leave_chat.assert_awaited_once()
            # The sent instruction is the wrong_place message
            sent = mock_notify.call_args.args[1]
            assert "instead of the discussion group" in sent

    @pytest.mark.asyncio
    async def test_not_member_after_window_leaves(self):
        """Discussion exists but bot never becomes member → wrong_place + leave."""
        chat = _make_chat()
        bot = _make_bot()
        bot.get_chat = AsyncMock(
            side_effect=lambda chat_id: MagicMock(
                id=chat_id, title="Discussion", username="discuss", linked_chat_id=None
            )
        )

        with (
            patch(
                "src.app.handlers.message.channel_management._resolve_linked_discussion_id",
                new_callable=AsyncMock,
                return_value=-1002222222222,
            ),
            patch(
                "src.app.handlers.message.channel_management._poll_discussion_membership",
                new_callable=AsyncMock,
                return_value=False,
            ),
            patch(
                "src.app.handlers.message.channel_management.notify_channel_admins",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_notify,
        ):
            await notify_channel_admins_and_leave(chat, bot)

            bot.leave_chat.assert_awaited_once()
            sent = mock_notify.call_args.args[1]
            assert "instead of the discussion group" in sent


class TestDecisionFlowProtectBranch:
    """Bot is (or becomes) a member of the discussion group → stay + protect."""

    @pytest.mark.asyncio
    async def test_member_stays_and_registers(self):
        """Member → no leave, discussion group registered, discussion_added message sent."""
        chat = _make_chat()
        bot = _make_bot()

        with (
            patch(
                "src.app.handlers.message.channel_management._resolve_linked_discussion_id",
                new_callable=AsyncMock,
                return_value=-1002222222222,
            ),
            patch(
                "src.app.handlers.message.channel_management._poll_discussion_membership",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.database.upsert_awaiting_rights_group",
                new_callable=AsyncMock,
            ) as mock_upsert,
        ):
            bot.get_chat = AsyncMock(
                side_effect=lambda chat_id: MagicMock(
                    id=chat_id,
                    title="My Discussion" if chat_id != chat.id else chat.title,
                    username="discuss" if chat_id != chat.id else None,
                    linked_chat_id=None,
                )
            )
            await notify_channel_admins_and_leave(chat, bot)

            # Never leaves
            bot.leave_chat.assert_not_awaited()
            # Registers the discussion group as awaiting-rights
            mock_upsert.assert_awaited_once_with(
                -1002222222222, "My Discussion", "discuss"
            )
            # Sent message is the discussion_added (protect) instruction
            sent = bot.send_message.call_args.args[1]
            assert "promote me to" in sent or "administrator" in sent
            assert "instead of the discussion group" not in sent

    @pytest.mark.asyncio
    async def test_dm_failure_fallback_posts_in_discussion(self):
        """Owner DM fails → notice posted into the discussion group itself."""
        chat = _make_chat()
        bot = _make_bot()
        bot.send_message = AsyncMock(side_effect=Exception("can't message user"))

        with (
            patch(
                "src.app.handlers.message.channel_management._resolve_linked_discussion_id",
                new_callable=AsyncMock,
                return_value=-1002222222222,
            ),
            patch(
                "src.app.handlers.message.channel_management._poll_discussion_membership",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.database.upsert_awaiting_rights_group",
                new_callable=AsyncMock,
            ),
        ):
            bot.get_chat = AsyncMock(
                side_effect=lambda chat_id: MagicMock(
                    id=chat_id,
                    title="Discussion",
                    username=None,
                    linked_chat_id=None,
                )
            )
            await notify_channel_admins_and_leave(chat, bot)

            # Never leaves despite DM failure
            bot.leave_chat.assert_not_awaited()
            # The fallback posted into the discussion group (-1002222222222)
            posted = [c for c in bot.send_message.call_args_list if c.args[0] == -1002222222222]
            assert posted, "expected a message posted into the discussion group"


class TestDecisionFlowUserbotFallback:
    """Primary flow fails with TelegramForbiddenError → userbot DM fallback."""

    @pytest.mark.asyncio
    async def test_forbidden_fallback_userbot(self):
        """When leave raises TelegramForbiddenError and adding_user has username, userbot DM is attempted."""
        chat = _make_chat(title=".")
        bot = _make_bot(
            leave_side_effect=TelegramForbiddenError(
                MagicMock(), "Forbidden: bot is not a member of the channel chat"
            )
        )

        adding_user = MagicMock()
        adding_user.id = 12345
        adding_user.username = "channeladmin"
        adding_user.is_bot = False

        with (
            patch(
                "src.app.handlers.message.channel_management._resolve_linked_discussion_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.app.handlers.message.channel_management.get_discussion_username",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.app.handlers.message.channel_management.send_userbot_dm",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_send_userbot,
        ):
            await notify_channel_admins_and_leave(chat, bot, adding_user=adding_user)

            mock_send_userbot.assert_called_once()
            call_kwargs = mock_send_userbot.call_args.kwargs
            assert call_kwargs["username"] == "channeladmin"
            assert call_kwargs["user_id"] == 12345
            assert "@ai_antispam_blocker_bot" in call_kwargs["message"]

    @pytest.mark.asyncio
    async def test_forbidden_no_username_skips_userbot(self):
        """When adding_user has no username, userbot DM is not attempted."""
        chat = _make_chat()
        bot = _make_bot(
            leave_side_effect=TelegramForbiddenError(
                MagicMock(), "Forbidden: bot is not a member"
            )
        )

        adding_user = MagicMock()
        adding_user.id = 12345
        adding_user.username = None  # No username
        adding_user.is_bot = False

        with (
            patch(
                "src.app.handlers.message.channel_management._resolve_linked_discussion_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.app.handlers.message.channel_management.get_discussion_username",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.app.handlers.message.channel_management.send_userbot_dm",
                new_callable=AsyncMock,
            ) as mock_send_userbot,
        ):
            await notify_channel_admins_and_leave(chat, bot, adding_user=adding_user)

            mock_send_userbot.assert_not_called()

    @pytest.mark.asyncio
    async def test_forbidden_does_not_raise(self):
        """notify_channel_admins_and_leave does not propagate TelegramForbiddenError."""
        chat = _make_chat()
        bot = _make_bot(
            leave_side_effect=TelegramForbiddenError(
                MagicMock(), "Forbidden: bot is not a member"
            )
        )

        with (
            patch(
                "src.app.handlers.message.channel_management._resolve_linked_discussion_id",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.app.handlers.message.channel_management.get_discussion_username",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.app.handlers.message.channel_management.send_userbot_dm",
                new_callable=AsyncMock,
                return_value=True,
            ),
        ):
            # Should not raise
            await notify_channel_admins_and_leave(chat, bot, adding_user=None)


class TestPollDiscussionMembership:
    """Poll helper: returns True when member appears, False after window."""

    @pytest.mark.asyncio
    async def test_returns_true_when_member(self):
        """First successful member result flips the branch."""
        from src.app.handlers.message.channel_management import _poll_discussion_membership

        bot = AsyncMock()
        bot.id = 7574715711
        bot.get_chat_member = AsyncMock(
            return_value=MagicMock(status="member")
        )

        result = await _poll_discussion_membership(
            -1002222222222, bot, attempts=3, interval=0.01
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_after_window(self):
        """No member status within the window → False."""
        from src.app.handlers.message.channel_management import _poll_discussion_membership

        bot = AsyncMock()
        bot.id = 7574715711
        bot.get_chat_member = AsyncMock(
            return_value=MagicMock(status="left")
        )

        result = await _poll_discussion_membership(
            -1002222222222, bot, attempts=3, interval=0.01
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_treated_as_not_member(self):
        """get_chat_member raising (e.g. not in chat yet) keeps polling."""
        from src.app.handlers.message.channel_management import _poll_discussion_membership

        bot = AsyncMock()
        bot.id = 7574715711
        bot.get_chat_member = AsyncMock(
            side_effect=Exception("user not found")
        )

        result = await _poll_discussion_membership(
            -1002222222222, bot, attempts=3, interval=0.01
        )
        assert result is False
