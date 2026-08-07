import pytest
import logging
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from aiogram import types

from src.app.handlers.private_handlers import (
    _append_spam_cleanup_tasks,
    extract_original_message_info,
)


class TestExtractOriginalMessageInfo:
    """Test the extract_original_message_info function with message_lookup_cache integration."""

    @pytest.fixture
    def mock_callback_message(self):
        """Create a mock callback message with a forwarded message."""
        # Create mock forwarded message
        forwarded_message = MagicMock(spec=types.Message)
        forwarded_message.text = "Test spam message"
        forwarded_message.caption = None
        forwarded_message.forward_date = datetime.now(timezone.utc)
        forwarded_message.date = datetime.now(timezone.utc)

        # Mock forward origin (user type)
        origin = MagicMock(spec=types.MessageOriginUser)
        origin.type = "user"
        sender_user = MagicMock(spec=types.User)
        sender_user.id = 12345
        sender_user.full_name = "Spammer User"
        sender_user.username = "spammeruser"
        origin.sender_user = sender_user
        forwarded_message.forward_origin = origin
        forwarded_message.forward_from = None

        # Create mock callback message
        callback_message = MagicMock(spec=types.Message)
        callback_message.reply_to_message = forwarded_message

        return callback_message

    @pytest.fixture
    def mock_bot_get_chat(self):
        """Mock bot.get_chat for user bio retrieval."""
        with patch("src.app.handlers.private_handlers.bot") as mock_bot:
            mock_chat = MagicMock()
            mock_chat.bio = "Spammer bio"
            mock_bot.get_chat = AsyncMock(return_value=mock_chat)
            yield mock_bot

    @pytest.mark.asyncio
    async def test_message_lookup_success(
        self, mock_callback_message, mock_bot_get_chat
    ):
        """Test successful message_lookup_cache lookup when forward metadata lacks group info."""
        admin_id = 99999

        # Mock admin groups
        with patch(
            "src.app.handlers.private_handlers.get_admin_groups", new_callable=AsyncMock
        ) as mock_get_groups:
            mock_get_groups.return_value = [{"id": 1001}, {"id": 1002}, {"id": 1003}]

            # Mock successful DB lookup
            with patch(
                "src.app.handlers.private_handlers.find_message_by_text_and_user",
                new_callable=AsyncMock,
            ) as mock_lookup:
                mock_lookup.return_value = {
                    "chat_id": 1001,
                    "message_id": 555,
                    "user_id": 12345,
                    "reply_to_text": None,
                    "stories_context": None,
                    "account_signals_context": None,
                }

                result = await extract_original_message_info(
                    mock_callback_message, admin_id
                )

                # Verify the result includes lookup data
                assert result["group_chat_id"] == 1001
                assert result["group_message_id"] == 555
                assert result["user_id"] == 12345
                assert result["username"] == "spammeruser"
                assert result["name"] == "Spammer User"
                assert result["bio"] == "Spammer bio"
                assert result["text"] == "Test spam message"

                # Verify lookup was called with correct parameters
                mock_lookup.assert_called_once()
                call_kwargs = mock_lookup.call_args[1]
                assert call_kwargs["message_text"] == "Test spam message"
                assert call_kwargs["admin_chat_ids"] == [1001, 1002, 1003]
                assert call_kwargs["user_id"] == 12345

    @pytest.mark.asyncio
    async def test_message_lookup_miss(self, mock_callback_message, mock_bot_get_chat):
        """Test message lookup when no matching message is found in cache."""
        admin_id = 99999

        # Mock admin groups
        with patch(
            "src.app.handlers.private_handlers.get_admin_groups", new_callable=AsyncMock
        ) as mock_get_groups:
            mock_get_groups.return_value = [{"id": 1001}, {"id": 1002}]

            # Mock DB lookup returning None
            with patch(
                "src.app.handlers.private_handlers.find_message_by_text_and_user",
                new_callable=AsyncMock,
            ) as mock_lookup:
                mock_lookup.return_value = None

                result = await extract_original_message_info(
                    mock_callback_message, admin_id
                )

                # Verify group IDs are still None (lookup failed)
                assert result["group_chat_id"] is None
                assert result["group_message_id"] is None
                assert result["user_id"] == 12345
                assert result["username"] == "spammeruser"
                assert result["text"] == "Test spam message"

    @pytest.mark.asyncio
    async def test_admin_has_no_groups(self, mock_callback_message, mock_bot_get_chat):
        """Test when admin has no managed groups (lookup skipped)."""
        admin_id = 99999

        # Mock empty admin groups
        with patch(
            "src.app.handlers.private_handlers.get_admin_groups", new_callable=AsyncMock
        ) as mock_get_groups:
            mock_get_groups.return_value = []

            # Message lookup should not be called
            with patch(
                "src.app.handlers.private_handlers.find_message_by_text_and_user",
                new_callable=AsyncMock,
            ) as mock_lookup:
                result = await extract_original_message_info(
                    mock_callback_message, admin_id
                )

                # Verify lookup was not called and group IDs are None
                mock_lookup.assert_not_called()
                assert result["group_chat_id"] is None
                assert result["group_message_id"] is None
                assert result["user_id"] == 12345
                assert result["username"] == "spammeruser"

    @pytest.mark.asyncio
    async def test_channel_forward_metadata_available(self, mock_bot_get_chat):
        """Test that message lookup is not called when forward metadata provides group info."""
        admin_id = 99999

        # Create mock with channel forward metadata
        callback_message = MagicMock(spec=types.Message)
        forwarded_message = MagicMock(spec=types.Message)
        forwarded_message.text = "Channel message"
        forwarded_message.forward_date = datetime.now(timezone.utc)

        # Mock channel origin
        origin = MagicMock(spec=types.MessageOriginChannel)
        origin.type = "channel"
        chat = MagicMock(spec=types.Chat)
        chat.id = 2001
        chat.title = "Spam Channel"
        origin.chat = chat
        origin.message_id = 777
        forwarded_message.forward_origin = origin
        forwarded_message.forward_from = None

        callback_message.reply_to_message = forwarded_message

        # Message lookup should not be called since we have metadata
        with patch(
            "src.app.handlers.private_handlers.get_admin_groups", new_callable=AsyncMock
        ) as mock_get_groups:
            with patch(
                "src.app.handlers.private_handlers.find_message_by_text_and_user",
                new_callable=AsyncMock,
            ) as mock_lookup:
                result = await extract_original_message_info(callback_message, admin_id)

                # Verify we got the info from forward metadata
                assert result["group_chat_id"] == 2001
                assert result["group_message_id"] == 777
                assert result["name"] == "Spam Channel"

                # Verify message lookup was not called
                mock_lookup.assert_not_called()
                mock_get_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_message_lookup_hidden_user(self, mock_bot_get_chat):
        """Test message lookup succeeds and can recover user_id when forward has hidden user."""
        admin_id = 99999

        # Create mock with hidden user forward (no user_id available from forward)
        callback_message = MagicMock(spec=types.Message)
        forwarded_message = MagicMock(spec=types.Message)
        forwarded_message.text = "Hidden user message"
        forwarded_message.forward_date = datetime.now(timezone.utc)
        forwarded_message.forward_from = None

        # Mock hidden user origin
        origin = MagicMock()
        origin.type = "hidden_user"
        origin.sender_user_name = "Hidden User"
        forwarded_message.forward_origin = origin

        callback_message.reply_to_message = forwarded_message

        # Mock admin groups
        with patch(
            "src.app.handlers.private_handlers.get_admin_groups", new_callable=AsyncMock
        ) as mock_get_groups:
            mock_get_groups.return_value = [{"id": 1001}, {"id": 1002}]

            # Mock successful DB lookup (recover user_id from cache)
            with patch(
                "src.app.handlers.private_handlers.find_message_by_text_and_user",
                new_callable=AsyncMock,
            ) as mock_lookup:
                mock_lookup.return_value = {
                    "chat_id": 1001,
                    "message_id": 888,
                    "user_id": 67890,
                    "reply_to_text": None,
                    "stories_context": None,
                    "account_signals_context": None,
                }

                result = await extract_original_message_info(callback_message, admin_id)

                # Verify the result includes lookup data and recovered user_id
                assert result["group_chat_id"] == 1001
                assert result["group_message_id"] == 888
                assert result["user_id"] == 67890
                assert result["text"] == "Hidden user message"

                # Verify lookup was called with user_id=None
                mock_lookup.assert_called_once()
                assert mock_lookup.call_args[1]["user_id"] is None

    @pytest.mark.asyncio
    async def test_no_user_id_in_forward(self):
        """Test handling when forwarded message has no user information."""
        admin_id = 99999

        # Create mock without user information
        callback_message = MagicMock(spec=types.Message)
        forwarded_message = MagicMock(spec=types.Message)
        forwarded_message.text = "Anonymous message"
        forwarded_message.forward_from = None
        forwarded_message.forward_origin = None  # No origin info

        callback_message.reply_to_message = forwarded_message

        # Should raise OriginalMessageExtractionError
        with pytest.raises(Exception):  # OriginalMessageExtractionError
            await extract_original_message_info(callback_message, admin_id)


class TestAppendSpamCleanupTasks:
    """Cleanup warnings must carry forward_origin_type + candidate_chats in the message."""

    def test_user_missing_warning_includes_context(self, caplog):
        info = {
            "user_id": None,
            "group_chat_id": None,
            "group_message_id": None,
            "forward_origin_type": "hidden_user",
            "candidate_chats": 1,
        }
        with caplog.at_level(logging.WARNING, logger="app.handlers.private_handlers"):
            _append_spam_cleanup_tasks([], info)
        assert "User ID not found in info" in caplog.text
        assert "forward_origin_type=hidden_user" in caplog.text
        assert "candidate_chats=1" in caplog.text

    def test_group_missing_warning_includes_context(self, caplog):
        info = {
            "user_id": 12345,
            "group_chat_id": None,
            "group_message_id": None,
            "forward_origin_type": "hidden_user",
            "candidate_chats": 2,
        }
        with caplog.at_level(logging.WARNING, logger="app.handlers.private_handlers"):
            _append_spam_cleanup_tasks([], info)
        assert "Group chat ID or message ID not found" in caplog.text
        assert "forward_origin_type=hidden_user" in caplog.text
        assert "candidate_chats=2" in caplog.text

    def test_warnings_without_context_render_none(self, caplog):
        """No origin/lookup ran -> fields absent from info -> render as None, not crash."""
        info = {
            "user_id": None,
            "group_chat_id": None,
            "group_message_id": None,
            "forward_origin_type": None,
            "candidate_chats": None,
        }
        with caplog.at_level(logging.WARNING, logger="app.handlers.private_handlers"):
            _append_spam_cleanup_tasks([], info)
        assert "User ID not found in info" in caplog.text
        assert "forward_origin_type=None" in caplog.text
        assert "candidate_chats=None" in caplog.text

    def test_user_present_no_warning_task_appended(self):
        info = {
            "user_id": 12345,
            "group_chat_id": None,
            "group_message_id": None,
            "forward_origin_type": "user",
            "candidate_chats": None,
        }
        tasks = []
        _append_spam_cleanup_tasks(tasks, info)
        assert len(tasks) == 1  # removal task only, no user warning
