from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Chat, Message, User

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
async def test_handle_spam_ignore_callback_duplicate_tap_logs_info(
    caplog,
):
    """
    Duplicate callback (row already confirmed): INFO level, no unban/add/set_moderation_events,
    message still edited to '✅ added' — same UX as success.
    """
    import logging

    callback = AsyncMock(spec=CallbackQuery)
    callback.data = "mark_as_not_spam:77"
    callback.from_user = User(id=999, is_bot=False, first_name="Admin")
    callback.message = AsyncMock(spec=Message)
    callback.message.chat = Chat(id=456, type="supergroup")
    callback.message.message_id = 111
    callback.message.text = "Spam message content"

    callback.answer = AsyncMock()

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
            return_value={"already_confirmed": True, "admin_id": 999},
        ) as mock_confirm,
        patch(
            "src.app.handlers.callback_handlers.add_member",
            new_callable=AsyncMock,
        ) as mock_add_member,
        patch(
            "src.app.handlers.callback_handlers.set_moderation_events",
            new_callable=AsyncMock,
        ) as mock_set_moderation,
    ):
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.unban_chat_member = AsyncMock()

        with caplog.at_level(logging.INFO, logger="src.app.handlers.callback_handlers"):
            result = await handle_spam_ignore_callback(callback)

        assert result == "callback_marked_as_not_spam"
        mock_confirm.assert_called_once_with(77, 999)
        # No unban/add/set_moderation_events on the duplicate path
        mock_bot.unban_chat_member.assert_not_called()
        mock_add_member.assert_not_called()
        mock_set_moderation.assert_not_called()
        # Message edited to '✅ added' regardless
        mock_bot.edit_message_text.assert_called_once()

    assert any(
        r.levelname == "INFO"
        and "77" in r.getMessage()
        and "already confirmed" in r.getMessage()
        and "999" in r.getMessage()
        for r in caplog.records
    )
    assert not any(r.levelname == "WARNING" for r in caplog.records)


@pytest.mark.asyncio
async def test_handle_spam_ignore_callback_genuine_missing_logs_warning(
    caplog,
):
    """
    Genuine missing (no row): WARNING level (honors the revert — don't lose genuine
    situations), no unban/add calls, message still edited.
    """
    import logging

    callback = AsyncMock(spec=CallbackQuery)
    callback.data = "mark_as_not_spam:55"
    callback.from_user = User(id=999, is_bot=False, first_name="Admin")
    callback.message = AsyncMock(spec=Message)
    callback.message.chat = Chat(id=456, type="supergroup")
    callback.message.message_id = 111
    callback.message.text = "Spam message content"

    callback.answer = AsyncMock()

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
            return_value=None,
        ) as mock_confirm,
        patch(
            "src.app.handlers.callback_handlers.add_member",
            new_callable=AsyncMock,
        ) as mock_add_member,
        patch(
            "src.app.handlers.callback_handlers.set_moderation_events",
            new_callable=AsyncMock,
        ) as mock_set_moderation,
    ):
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.unban_chat_member = AsyncMock()

        with caplog.at_level(
            logging.WARNING, logger="src.app.handlers.callback_handlers"
        ):
            result = await handle_spam_ignore_callback(callback)

        assert result == "callback_marked_as_not_spam"
        mock_confirm.assert_called_once_with(55, 999)
        # No unban/add/set_moderation_events on the missing path
        mock_bot.unban_chat_member.assert_not_called()
        mock_add_member.assert_not_called()
        mock_set_moderation.assert_not_called()
        # Message edited to '✅ added' regardless
        mock_bot.edit_message_text.assert_called_once()

    assert any(
        r.levelname == "WARNING"
        and "55" in r.getMessage()
        and "not found" in r.getMessage()
        for r in caplog.records
    )


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


@pytest.mark.asyncio
async def test_handle_spam_confirm_callback_delete_failure_logs_title_username(
    caplog,
):
    """
    Delete-failure log line carries the chat title/username from callback.message.chat
    (no extra API call needed — the chat object is already in scope).
    """
    import logging

    from aiogram.exceptions import TelegramBadRequest

    callback = AsyncMock(spec=CallbackQuery)
    callback.answer = AsyncMock()
    callback.data = "delete_spam_message:12345:67890:111"
    callback.from_user = User(id=999, is_bot=False, first_name="Admin")
    callback.message = AsyncMock(spec=Message)
    callback.message.chat = Chat(
        id=456, type="supergroup", title="Test Group", username="testgroup"
    )
    callback.message.message_id = 222
    callback.message.text = "⚠️ INTRUSION! Violator: @spammer"

    with (
        patch("src.app.handlers.callback_handlers.bot") as mock_bot,
        patch(
            "src.app.handlers.callback_handlers.get_admin",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.handlers.callback_handlers.get_group",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "src.app.handlers.callback_handlers.ban_user_for_spam",
            new_callable=AsyncMock,
        ),
        patch(
            "src.app.handlers.callback_handlers.confirm_pending_example_as_spam",
            new_callable=AsyncMock,
        ),
    ):
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.delete_message = AsyncMock(
            side_effect=TelegramBadRequest(
                method="deleteMessage", message="message to delete not found"
            )
        )

        with caplog.at_level(logging.INFO):
            result = await handle_spam_confirm_callback(callback)

        assert result == "callback_spam_message_deleted"

    assert any(
        "delete spam message already resolved" in r.getMessage()
        for r in caplog.records
    )


# =========================================================================
# TDD test 1d — callback delete block + TelegramForbiddenError
# =========================================================================
@pytest.mark.asyncio
async def test_callback_delete_permission_error_answers_user_with_error():
    """callback.delete_spam_message with permission error answers user, no crash."""
    from aiogram.exceptions import TelegramForbiddenError

    callback = AsyncMock(spec=CallbackQuery)
    callback.data = "delete_spam_message:67890:-1001234567890:111111"
    callback.from_user = User(id=999, is_bot=False, first_name="Admin")
    callback.message = AsyncMock(spec=Message)
    callback.message.chat = Chat(id=-1001234567890, type="supergroup")
    callback.message.message_id = 222222

    with (
        patch("src.app.handlers.callback_handlers.bot") as mock_bot,
        patch(
            "src.app.handlers.callback_handlers.get_admin",
            new_callable=AsyncMock,
            return_value=MagicMock(is_active=True, language_code="en"),
        ),
        patch(
            "src.app.handlers.callback_handlers.get_group",
            new_callable=AsyncMock,
            return_value=MagicMock(admin_ids=[999]),
        ),
        patch(
            "src.app.handlers.callback_handlers.confirm_pending_example_as_spam",
            new_callable=AsyncMock,
        ),
        patch(
            "src.app.handlers.callback_handlers.ban_user_for_spam",
            new_callable=AsyncMock,
        ),
        patch(
            "src.app.handlers.callback_handlers.bot.edit_message_text",
            new_callable=AsyncMock,
        ),
    ):
        mock_bot.delete_message = AsyncMock(
            side_effect=TelegramForbiddenError(
                method=MagicMock(), message="bot was kicked from the group"
            )
        )

        result = await handle_spam_confirm_callback(callback)

        # Should NOT crash
        assert result is not None
        # callback.answer should have been called with error text
        assert callback.answer.called


# =========================================================================
# #52 — help callbacks must be answered even when the edit fails
#
# A repeat tap ("message is not modified") and an expired query both used to
# escape the handler. The edit ran BEFORE answer(), so when it raised the
# callback was never answered: the member got a spurious failure popup and the
# dispatcher logged two ERROR lines per tap. These legs assert the PROPERTY —
# the callback is answered on the failure path — not the implementation.
# =========================================================================


def _help_callback(data: str) -> AsyncMock:
    callback = AsyncMock(spec=CallbackQuery)
    callback.data = data
    callback.from_user = User(id=999, is_bot=False, first_name="Admin")
    callback.message = AsyncMock(spec=Message)
    callback.message.chat = Chat(id=456, type="private")
    callback.message.message_id = 111
    callback.answer = AsyncMock()
    return callback


def _patch_help_deps():
    return patch(
        "src.app.handlers.callback_handlers.get_admin",
        new_callable=AsyncMock,
        return_value=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler_name", "data"),
    [
        ("handle_help_pages", "help.moderation"),
        ("handle_help_back", "help_back"),
    ],
)
async def test_help_repeat_tap_still_answers_callback(handler_name, data):
    """Second tap of an already-displayed page: edit raises 'message is not modified'."""
    from src.app.handlers import callback_handlers

    handler = getattr(callback_handlers, handler_name)
    callback = _help_callback(data)
    callback.message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method="editMessageText",
            message="Bad Request: message is not modified",
        )
    )

    with _patch_help_deps():
        try:
            result = await handler(callback)
        except TelegramBadRequest as exc:
            result = f"<propagated {exc!r}>"

    assert callback.answer.called, (
        f"{handler_name} left the callback unanswered when edit_text raised "
        "TelegramBadRequest('message is not modified') "
        f"(returned {result!r}). Telegram keeps the client spinner up, the "
        "member taps again, and each tap logs two dispatcher ERRORs (#52)."
    )
    assert not str(result).startswith("<propagated"), (
        f"{handler_name} let the expected repeat-tap error escape to the "
        f"dispatcher: {result}"
    )


@pytest.mark.asyncio
async def test_help_page_expired_query_does_not_escape():
    """A query Telegram already considers expired cannot be answered by anyone."""
    from src.app.handlers import callback_handlers

    callback = _help_callback("help.payment")
    callback.answer = AsyncMock(
        side_effect=TelegramBadRequest(
            method="answerCallbackQuery",
            message=(
                "Bad Request: query is too old and response timeout expired "
                "or query ID is invalid"
            ),
        )
    )
    callback.message.edit_text = AsyncMock()

    with _patch_help_deps():
        try:
            result = await callback_handlers.handle_help_pages(callback)
        except TelegramBadRequest as exc:
            result = f"<propagated {exc!r}>"

    assert not str(result).startswith("<propagated"), (
        "an expired query must not escape to the dispatcher as an unhandled "
        f"exception: {result}"
    )


@pytest.mark.asyncio
async def test_lang_set_repeat_tap_does_not_escape():
    """Same defect class as the help handlers, found by the sibling audit.

    Re-tapping the same language button re-issues an identical edit, which
    Telegram refuses as a no-op. The answer must still happen and the no-op must
    not reach the dispatcher as an unhandled exception (#52).
    """
    from src.app.handlers import callback_handlers

    callback = _help_callback("lang_set:en")
    callback.message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method="editMessageText",
            message="Bad Request: message is not modified",
        )
    )

    with (
        _patch_help_deps(),
        patch(
            "src.app.handlers.callback_handlers.update_admin_language",
            new_callable=AsyncMock,
        ),
    ):
        try:
            result = await callback_handlers.handle_lang_set_callback(callback)
        except TelegramBadRequest as exc:
            result = f"<propagated {exc!r}>"

    assert callback.answer.called, (
        "handle_lang_set_callback left the callback unanswered when edit_text "
        f"raised TelegramBadRequest('message is not modified') (returned {result!r})."
    )
    assert not str(result).startswith("<propagated"), (
        "the expected repeat-tap no-op escaped to the dispatcher as an "
        f"unhandled exception: {result}"
    )
