"""Tests for channel management decision flow (protect discussion vs leave)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramForbiddenError

from src.app.handlers.message.channel_management import (
    build_channel_discussion_added_message,
    build_channel_instruction_message,
    build_channel_instruction_userbot_message,
    notify_channel_admins,
    notify_channel_admins_and_leave,
    _notify_wrong_place_and_leave,
    _resolve_linked_discussion_id,
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
            # Registers the discussion group as awaiting-rights (linked to channel)
            mock_upsert.assert_awaited_once_with(
                -1002222222222,
                "My Discussion",
                "discuss",
                linked_channel_id=-1001297263491,
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
                "src.app.handlers.message.channel_management.asyncio.sleep",
                new_callable=AsyncMock,
            ),
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
                "src.app.handlers.message.channel_management.asyncio.sleep",
                new_callable=AsyncMock,
            ),
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
                "src.app.handlers.message.channel_management.asyncio.sleep",
                new_callable=AsyncMock,
            ),
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


class TestProtectedChannelGuard:
    """handle_channel_post: protected channel never self-leaves."""

    @pytest.mark.asyncio
    async def test_protected_channel_ignored_no_leave(self):
        """Protected channel → ignored, no notify/leave."""
        from src.app.handlers.message.channel_management import (
            handle_channel_post,
            _protected_channel_ids,
        )

        _protected_channel_ids.add(-1001111111111)
        try:
            message = MagicMock()
            message.chat.id = -1001111111111

            with patch(
                "src.app.handlers.message.channel_management._is_protected_channel",
                new_callable=AsyncMock,
                return_value=True,
            ):
                result = await handle_channel_post(message)
            assert result == "channel_post_ignored_protected"
        finally:
            _protected_channel_ids.discard(-1001111111111)

    @pytest.mark.asyncio
    async def test_unprotected_channel_triggers_leave_flow(self):
        """Unprotected channel → existing leave flow fires."""
        from src.app.handlers.message.channel_management import (
            handle_channel_post,
            _protected_channel_ids,
        )

        _protected_channel_ids.discard(-1009999999999)
        try:
            message = MagicMock()
            message.chat.id = -1009999999999

            with (
                patch(
                    "src.app.handlers.message.channel_management._is_protected_channel",
                    new_callable=AsyncMock,
                    return_value=False,
                ),
                patch(
                    "src.app.handlers.message.channel_management.notify_channel_admins_and_leave",
                    new_callable=AsyncMock,
                ) as mock_notify,
            ):
                result = await handle_channel_post(message)
            assert result == "channel_post_left_channel"
            mock_notify.assert_awaited_once()
        finally:
            _protected_channel_ids.discard(-1009999999999)

    @pytest.mark.asyncio
    async def test_channel_post_does_not_settle(self):
        """handle_channel_post decides immediately — no composite settle/dedupe.

        The settle window + _pending_composites registry live ONLY in the
        status_handlers my_chat_member branch. handle_channel_post must NOT
        wait for Telegram propagation: a post is a real message in a channel the
        bot was added to long ago, not part of the add-time composite batch.
        This test proves channel_post never reads or writes the registry and
        calls the leave flow directly (no sleep before the decision).
        """
        from src.app.handlers.message.channel_management import (
            handle_channel_post,
            _protected_channel_ids,
        )

        _protected_channel_ids.discard(-1008888888888)
        try:
            message = MagicMock()
            message.chat.id = -1008888888888

            # Registry access would raise: channel_post must never touch it.
            class _RegistryBomb(dict):
                def __getitem__(self, k):
                    raise AssertionError("channel_post must not read _pending_composites")

            with (
                patch(
                    "src.app.handlers.message.channel_management._is_protected_channel",
                    new_callable=AsyncMock,
                    return_value=False,
                ),
                patch(
                    "src.app.handlers.message.channel_management.notify_channel_admins_and_leave",
                    new_callable=AsyncMock,
                ) as mock_notify,
                patch(
                    "src.app.handlers.status_handlers._pending_composites",
                    _RegistryBomb(),
                ),
                patch(
                    "src.app.handlers.status_handlers._composites_lock",
                    MagicMock(side_effect=AssertionError("channel_post must not lock composites")),
                ),
                patch(
                    "src.app.handlers.status_handlers.asyncio.sleep",
                    AsyncMock(side_effect=AssertionError("channel_post must not settle")),
                ),
            ):
                result = await handle_channel_post(message)

            assert result == "channel_post_left_channel"
            mock_notify.assert_awaited_once()
        finally:
            _protected_channel_ids.discard(-1008888888888)

    @pytest.mark.asyncio
    async def test_is_protected_channel_db_recheck_on_miss(self):
        """Miss in memory → DB re-check discovers and caches the channel."""
        from src.app.handlers.message.channel_management import (
            _is_protected_channel,
            _protected_channel_ids,
        )

        _protected_channel_ids.discard(-1001231231231)
        with patch(
            "src.app.database.group_operations.get_protected_channel_ids",
            new_callable=AsyncMock,
            return_value=[-1001231231231],
        ):
            try:
                assert await _is_protected_channel(-1001231231231) is True
                assert -1001231231231 in _protected_channel_ids
            finally:
                _protected_channel_ids.discard(-1001231231231)

    @pytest.mark.asyncio
    async def test_seed_protected_channels_from_db(self):
        """Startup seeding loads distinct protected channel ids."""
        from src.app.handlers.message.channel_management import (
            _seed_protected_channels,
            _protected_channel_ids,
        )

        _protected_channel_ids.add(-1000000000001)  # stale entry
        with patch(
            "src.app.database.group_operations.get_protected_channel_ids",
            new_callable=AsyncMock,
            return_value=[-1005555555555, -1006666666666],
        ):
            await _seed_protected_channels()
        try:
            assert _protected_channel_ids == {-1005555555555, -1006666666666}
        finally:
            _protected_channel_ids.clear()

    @pytest.mark.asyncio
    async def test_seed_failure_sets_flag(self):
        """DB down at seeding -> _seed_failed set True."""
        import src.app.handlers.message.channel_management as cm

        cm._seed_failed = False
        with patch(
            "src.app.database.group_operations.get_protected_channel_ids",
            new_callable=AsyncMock,
            side_effect=Exception("db down"),
        ):
            await cm._seed_protected_channels()
        assert cm._seed_failed is True

    @pytest.mark.asyncio
    async def test_db_unavailable_refuses_to_leave(self):
        """DB unavailable on re-check -> skip leave, distinct tag, no notify."""
        from src.app.handlers.message.channel_management import (
            handle_channel_post,
            ProtectedChannelCheckUnavailable,
        )

        message = MagicMock()
        message.chat.id = -1008888888888
        message.chat.title = "Test Channel"
        message.chat.username = None

        with patch(
            "src.app.handlers.message.channel_management._is_protected_channel",
            new_callable=AsyncMock,
            side_effect=ProtectedChannelCheckUnavailable("db down"),
        ), patch(
            "src.app.handlers.message.channel_management.notify_channel_admins_and_leave",
            new_callable=AsyncMock,
        ) as mock_notify:
            result = await handle_channel_post(message)
        assert result == "channel_post_skipped_db_unavailable"
        mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_is_protected_channel_raises_on_db_error(self):
        """DB error in re-check raises ProtectedChannelCheckUnavailable (not False)."""
        from src.app.handlers.message.channel_management import (
            _is_protected_channel,
            _protected_channel_ids,
            ProtectedChannelCheckUnavailable,
        )

        _protected_channel_ids.discard(-1007777777777)
        with patch(
            "src.app.database.group_operations.get_protected_channel_ids",
            new_callable=AsyncMock,
            side_effect=Exception("db down"),
        ):
            with pytest.raises(ProtectedChannelCheckUnavailable):
                await _is_protected_channel(-1007777777777)
        _protected_channel_ids.discard(-1007777777777)




class TestWithForbiddenRetry:
    """Propagation-aware retry: Forbidden is transient within the add window."""

    @pytest.mark.asyncio
    async def test_get_chat_retry_then_success(self):
        """_resolve_linked_discussion_id retries Forbidden then succeeds (getChat)."""
        chat = _make_chat()
        bot = _make_bot()
        forbidden = TelegramForbiddenError(
            MagicMock(), "Forbidden: bot is not a member of the channel chat"
        )
        fresh = MagicMock(id=chat.id, title="C", linked_chat_id=-1002222222222)

        calls = {"n": 0}

        async def flaky_get_chat(chat_id):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise forbidden
            return fresh

        bot.get_chat = AsyncMock(side_effect=flaky_get_chat)
        with patch(
            "src.app.handlers.message.channel_management.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            linked = await _resolve_linked_discussion_id(chat, bot)

        assert linked == -1002222222222
        assert calls["n"] == 3
        mock_sleep.assert_awaited()
        assert mock_sleep.await_count == 2

    @pytest.mark.asyncio
    async def test_get_chat_administrators_retry_then_success(self):
        """notify_channel_admins retries Forbidden then succeeds (get_chat_administrators)."""
        chat = _make_chat()
        bot = _make_bot()
        forbidden = TelegramForbiddenError(
            MagicMock(), "Forbidden: bot is not a member of the channel chat"
        )

        calls = {"n": 0}

        async def flaky_get_admins(chat_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise forbidden
            return []

        bot.get_chat_administrators = AsyncMock(side_effect=flaky_get_admins)
        with patch(
            "src.app.handlers.message.channel_management.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep:
            notified = await notify_channel_admins(chat, "instruction", bot)

        assert notified == []
        assert calls["n"] == 2
        mock_sleep.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_leave_chat_retry_then_success(self):
        """_notify_wrong_place_and_leave retries leave_chat Forbidden then leaves."""
        chat = _make_chat()
        bot = _make_bot()
        forbidden = TelegramForbiddenError(
            MagicMock(), "Forbidden: bot is not a member of the channel chat"
        )

        calls = {"n": 0}

        async def flaky_leave(chat_id):
            calls["n"] += 1
            if calls["n"] == 1:
                raise forbidden
            return None

        bot.leave_chat = AsyncMock(side_effect=flaky_leave)
        with (
            patch(
                "src.app.handlers.message.channel_management.asyncio.sleep",
                new_callable=AsyncMock,
            ) as mock_sleep,
            patch(
                "src.app.handlers.message.channel_management.get_discussion_username",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "src.app.handlers.message.channel_management.notify_channel_admins",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            await _notify_wrong_place_and_leave(chat, bot)

        assert calls["n"] == 2
        mock_sleep.assert_awaited_once()
        bot.leave_chat.assert_awaited()

    @pytest.mark.asyncio
    async def test_leave_chat_all_fail_loud_error_and_userbot_fallback(self):
        """leave_chat all-fail → ERROR + logfire span channel_leave_failed, userbot fallback still attempted."""
        chat = _make_chat()
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
                "src.app.handlers.message.channel_management.asyncio.sleep",
                new_callable=AsyncMock,
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
            ),
            patch(
                "src.app.handlers.message.channel_management.send_userbot_dm",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_send_userbot,
            patch(
                "src.app.handlers.message.channel_management.logfire.span"
            ) as mock_span,
            patch(
                "src.app.handlers.message.channel_management.logger.error"
            ) as mock_error,
        ):
            await _notify_wrong_place_and_leave(chat, bot, adding_user=adding_user)

        # Loud failure: ERROR logged + channel_leave_failed span recorded
        mock_error.assert_called_once()
        mock_span.assert_called_once()
        span_name = mock_span.call_args.args[0]
        assert span_name == "channel_leave_failed"
        # Userbot fallback still attempted after the loud failure
        mock_send_userbot.assert_called_once()
        assert mock_send_userbot.call_args.kwargs["username"] == "channeladmin"
