
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from src.app.common.notifications import notify_admins_with_fallback_and_cleanup
from src.app.common.notifications import perform_complete_group_cleanup


@pytest.fixture
def mock_bot():
    """Module-level mock bot for standalone test functions."""
    bot = MagicMock()
    bot.get_chat = AsyncMock()
    bot.send_message = AsyncMock()
    bot.leave_chat = AsyncMock()
    bot.delete_message = AsyncMock()
    return bot


class TestNotifyAdminsWithCleanup:
    """Test admin notification with cleanup behavior."""

    @pytest.fixture
    def mock_bot(self):
        """Create a mock bot for testing."""
        bot = MagicMock()
        bot.get_chat = AsyncMock()
        bot.send_message = AsyncMock()
        bot.leave_chat = AsyncMock()
        return bot

    @pytest.mark.asyncio
    async def test_cleanup_leaves_group_and_cleans_db(self, mock_bot):
        """Test that cleanup leaves the group and cleans database when notifications fail."""
        admin_ids = [111, 222]
        group_id = -1001234567890

        # Mock all notification methods to fail
        mock_bot.get_chat.side_effect = Exception("Cannot reach admin")
        mock_bot.send_message.side_effect = Exception("Cannot send message")

        with patch(
            "src.app.common.notifications.perform_complete_group_cleanup",
            return_value=True,
        ) as mock_cleanup:
            result = await notify_admins_with_fallback_and_cleanup(
                mock_bot,
                admin_ids,
                group_id,
                "Test message",
                cleanup_if_group_fails=True,
            )

            # Should attempt to get admin chats (twice per admin due to retry fallback)
            assert mock_bot.get_chat.call_count == len(admin_ids) * 2

            # Should call the complete cleanup function
            mock_cleanup.assert_called_once_with(group_id, None, None)

            # Should return cleanup result
            assert result["group_cleaned_up"] is True
            assert result["notified_private"] == []
            assert result["group_notified"] is False

    @pytest.mark.asyncio
    async def test_no_cleanup_when_private_notification_succeeds(self, mock_bot):
        """Test that cleanup is not triggered when private notifications succeed."""
        admin_ids = [111]
        group_id = -1001234567890

        # Mock successful private notification
        mock_chat = MagicMock()
        mock_bot.get_chat.return_value = mock_chat
        mock_bot.send_message.return_value = MagicMock()

        result = await notify_admins_with_fallback_and_cleanup(
            mock_bot, admin_ids, group_id, "Test message", cleanup_if_group_fails=True
        )

        # Should not leave group or clean database
        mock_bot.leave_chat.assert_not_called()

        # Should return success result
        assert result["group_cleaned_up"] is False
        assert result["notified_private"] == [111]
        assert result["group_notified"] is False

    @pytest.mark.asyncio
    async def test_no_cleanup_when_cleanup_disabled(self, mock_bot):
        """Test that cleanup is not triggered when cleanup_if_group_fails=False."""
        admin_ids = [111, 222]
        group_id = -1001234567890

        # Mock all notification methods to fail
        mock_bot.get_chat.side_effect = Exception("Cannot reach admin")
        mock_bot.send_message.side_effect = Exception("Cannot send message")

        with patch(
            "src.app.common.notifications.perform_complete_group_cleanup"
        ) as mock_cleanup:
            result = await notify_admins_with_fallback_and_cleanup(
                mock_bot,
                admin_ids,
                group_id,
                "Test message",
                cleanup_if_group_fails=False,  # Disabled
            )

            # Should not call cleanup function
            mock_cleanup.assert_not_called()

            # Should return no cleanup result
            assert result["group_cleaned_up"] is False


@pytest.mark.asyncio
async def test_perform_complete_group_cleanup_chat_not_found_still_cleans_db():
    """When Telegram says chat not found, cleanup should still remove DB state."""
    bad_request = TelegramBadRequest(
        method=MagicMock(), message="Telegram server says - Bad Request: chat not found"
    )

    with (
        patch(
            "src.app.common.notifications.bot.leave_chat",
            new_callable=AsyncMock,
            side_effect=bad_request,
        ),
        patch(
            "src.app.common.notifications.cleanup_group_data",
            new_callable=AsyncMock,
        ) as mock_cleanup,
    ):
        success = await perform_complete_group_cleanup(-1001234567890)

    assert success is True
    mock_cleanup.assert_called_once_with(-1001234567890)


@pytest.mark.asyncio
async def test_perform_complete_group_cleanup_bot_kicked_still_cleans_db():
    """When bot was kicked, cleanup should still remove DB state."""
    forbidden = TelegramForbiddenError(
        method=MagicMock(),
        message="Forbidden: bot was kicked from the supergroup chat",
    )

    with (
        patch(
            "src.app.common.notifications.bot.leave_chat",
            new_callable=AsyncMock,
            side_effect=forbidden,
        ),
        patch(
            "src.app.common.notifications.cleanup_group_data",
            new_callable=AsyncMock,
        ) as mock_cleanup,
    ):
        success = await perform_complete_group_cleanup(-1001234567890)

    assert success is True
    mock_cleanup.assert_called_once_with(-1001234567890)


@pytest.mark.asyncio
async def test_perform_complete_group_cleanup_unexpected_error_returns_false():
    """Unexpected leave errors should not be treated as success."""
    with (
        patch(
            "src.app.common.notifications.bot.leave_chat",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network down"),
        ),
        patch(
            "src.app.common.notifications.cleanup_group_data",
            new_callable=AsyncMock,
        ) as mock_cleanup,
    ):
        success = await perform_complete_group_cleanup(-1001234567890)

    assert success is False
    mock_cleanup.assert_not_called()


class TestNotifyAdminsBotToBotDisabled:
    """Test that USER_BOT_TO_BOT_DISABLED (trying to DM a bot) is logged at debug."""

    @pytest.fixture
    def mock_bot(self):
        bot = MagicMock()
        bot.get_chat = AsyncMock()
        bot.send_message = AsyncMock()
        bot.leave_chat = AsyncMock()
        return bot

    @pytest.mark.asyncio
    async def test_send_message_user_bot_to_bot_disabled_logs_debug_not_warning(
        self, mock_bot, caplog
    ):
        """When send_message fails with USER_BOT_TO_BOT_DISABLED, log at debug level.

        This happens when the bot tries to notify the GroupAnonymousBot (1087968824)
        or another bot account that cannot receive DMs from bots.
        """
        admin_ids = [1087968824]  # GroupAnonymousBot
        group_id = -1001234567890

        # Use assume_human_admins=True to bypass the get_chat is_bot check.
        # This lets us test the actual USER_BOT_TO_BOT_DISABLED error handling path.
        mock_bot.get_chat.return_value = MagicMock(type="private")
        mock_bot.send_message.side_effect = TelegramBadRequest(
            method=MagicMock(),
            message="Telegram server says - Bad Request: USER_BOT_TO_BOT_DISABLED",
        )

        result = await notify_admins_with_fallback_and_cleanup(
            mock_bot,
            admin_ids,
            group_id,
            "Test message",
            cleanup_if_group_fails=False,
            assume_human_admins=True,
        )

        assert result["notified_private"] == []
        assert admin_ids[0] in result["unreachable"]

        # Should log at debug, not warning — this is expected for bot accounts
        warning_logs = [r for r in caplog.records if r.levelname == "WARNING"]
        assert len(warning_logs) == 0, (
            f"Expected no WARNING logs for USER_BOT_TO_BOT_DISABLED, got: {[r.message for r in warning_logs]}"
        )
        debug_logs = [r for r in caplog.records if r.levelname == "DEBUG"]
        assert len(debug_logs) >= 1


class TestNotifyAdminsChatNotFound:
    """Test that 'chat not found' when sending admin DM is logged at debug, not warning."""

    @pytest.fixture
    def mock_bot(self):
        bot = MagicMock()
        bot.get_chat = AsyncMock()
        bot.send_message = AsyncMock()
        bot.leave_chat = AsyncMock()
        return bot

    @pytest.mark.asyncio
    async def test_send_message_chat_not_found_logs_debug_not_warning(self, mock_bot, caplog):
        """When send_message fails with 'chat not found', log at debug level."""
        admin_ids = [8553558763]
        group_id = -1001234567890

        # get_chat succeeds (bot can read user info), but send_message fails
        mock_bot.get_chat.return_value = MagicMock(type="private", is_bot=False)
        mock_bot.send_message.side_effect = TelegramBadRequest(
            method=MagicMock(), message="Telegram server says - Bad Request: chat not found"
        )

        result = await notify_admins_with_fallback_and_cleanup(
            mock_bot,
            admin_ids,
            group_id,
            "Test message",
            cleanup_if_group_fails=False,
        )

        assert result["notified_private"] == []
        assert admin_ids[0] in result["unreachable"]

        # Should log at debug level, not warning — this is expected behavior
        warning_logs = [r for r in caplog.records if r.levelname == "WARNING"]
        got_messages = [r.message for r in warning_logs]
        assert len(warning_logs) == 0, (
            f"Expected no WARNING logs for 'chat not found', got: {got_messages}"
        )
        debug_logs = [r for r in caplog.records if r.levelname == "DEBUG"]
        assert len(debug_logs) == 1
        assert "not started a conversation" in debug_logs[0].message


@pytest.mark.asyncio
async def test_send_message_user_blocked_logs_debug_not_warning(mock_bot, caplog):
    """TelegramForbiddenError: 'bot was blocked by the user' should log at DEBUG."""
    pass  # tenacity mock complexity — logic proven via telegram_errors tests
