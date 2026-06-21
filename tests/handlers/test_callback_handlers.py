import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from aiogram.types import CallbackQuery, User, Message, Chat

from src.app.handlers.callback_handlers import (
    handle_spam_confirm_callback,
    handle_spam_ignore_callback,
)


@pytest.mark.asyncio
async def test_handle_spam_ignore_callback_answer_error():
    """
    Test that handle_spam_ignore_callback continues execution even if callback.answer fails
    (e.g. due to query being too old), and that admin_id is correctly bound.
    """
    callback = AsyncMock(spec=CallbackQuery)
    callback.data = "mark_as_not_spam:1"
    callback.from_user = User(id=999, is_bot=False, first_name="Admin")
    callback.message = AsyncMock(spec=Message)
    callback.message.chat = Chat(id=456, type="supergroup")
    callback.message.message_id = 111
    callback.message.text = "Spam message content"

    callback.answer.side_effect = Exception("Query is too old")

    with (
        patch("src.app.handlers.callback_handlers.bot") as mock_bot,
        patch(
            "src.app.handlers.callback_handlers.get_admin",
            new_callable=AsyncMock,
            return_value=MagicMock(is_active=True, language_code="en"),
        ),
        patch(
            "src.app.handlers.callback_handlers.confirm_pending_example_as_not_spam",
            new_callable=AsyncMock,
        ) as mock_confirm,
        patch(
            "src.app.handlers.callback_handlers.add_member",
            new_callable=AsyncMock,
        ),
        patch(
            "src.app.handlers.callback_handlers.set_moderation_events",
            new_callable=AsyncMock,
        ),
    ):
        mock_confirm.return_value = {
            "chat_id": 456,
            "message_id": 100,
            "effective_user_id": 123,
        }
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.unban_chat_member = AsyncMock()

        result = await handle_spam_ignore_callback(callback)

        assert result == "callback_marked_as_not_spam"
        mock_confirm.assert_called_once_with(1, 999)


@pytest.mark.asyncio
async def test_handle_spam_ignore_callback_confirm_raises():
    """
    Test that if confirm_pending_example_as_not_spam raises, handler returns error.
    """
    callback = AsyncMock(spec=CallbackQuery)
    callback.data = "mark_as_not_spam:1"
    callback.from_user = User(id=999, is_bot=False, first_name="Admin")
    callback.message = AsyncMock(spec=Message)

    with patch(
        "src.app.handlers.callback_handlers.confirm_pending_example_as_not_spam",
        new_callable=AsyncMock,
        side_effect=Exception("Network error"),
    ):
        result = await handle_spam_ignore_callback(callback)

        assert result == "callback_error_marking_not_spam"


@pytest.mark.asyncio
async def test_handle_spam_confirm_callback_deletes_and_bans():
    """
    When admin in notify mode clicks "Удалить", both the message is deleted
    and the spammer is banned. Also confirms the pending spam example as spam.
    Notification message is edited to remove keyboard and append confirmation line.
    """
    callback = AsyncMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.data = "delete_spam_message:12345:67890:111"
    callback.from_user = User(id=999, is_bot=False, first_name="Admin")
    callback.message = AsyncMock(spec=Message)
    callback.message.chat = Chat(id=456, type="private")
    callback.message.message_id = 222
    callback.message.text = "⚠️ INTRUSION! Violator: @spammer"

    with (
        patch("src.app.handlers.callback_handlers.bot") as mock_bot,
        patch(
            "src.app.handlers.callback_handlers.get_admin",
            new_callable=AsyncMock,
        ) as mock_get_admin,
        patch(
            "src.app.handlers.callback_handlers.get_group",
            new_callable=AsyncMock,
        ) as mock_get_group,
        patch(
            "src.app.handlers.callback_handlers.ban_user_for_spam",
            new_callable=AsyncMock,
        ) as mock_ban,
        patch(
            "src.app.handlers.callback_handlers.confirm_pending_example_as_spam",
            new_callable=AsyncMock,
        ) as mock_confirm_spam,
    ):
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.delete_message = AsyncMock()
        mock_get_admin.return_value = None
        mock_get_group.return_value = None  # Group not in DB - admin_ids=None

        result = await handle_spam_confirm_callback(callback)

        assert result == "callback_spam_message_deleted"
        mock_confirm_spam.assert_called_once_with(67890, 111, 999)
        mock_bot.delete_message.assert_called_once_with(67890, 111)
        mock_ban.assert_called_once_with(67890, 12345, None, group_title=None)
        # Callback Button UX Pattern: keyboard removed, confirmation appended
        mock_bot.edit_message_text.assert_called_once()
        call_kwargs = mock_bot.edit_message_text.call_args.kwargs
        assert call_kwargs["reply_markup"] is None
        assert "✅" in call_kwargs["text"] and "Spam deleted" in call_kwargs["text"]
