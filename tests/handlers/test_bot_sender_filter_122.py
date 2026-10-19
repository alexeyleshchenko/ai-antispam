"""#122 — в группе с несколькими ботами не реагируем на чужих ботов.

Два входа, обе двери закрыты:
1. `updates_filter` — сообщение бота не попадает в пайплайн модерации;
2. `delete_and_redirect_to_pm` — команда, адресованная другому боту
   (`/cmd@OtherBot`), не удаляется и не вызывает памятку про ЛС.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Chat, Message, User

from src.app.handlers.command_handlers import delete_and_redirect_to_pm
from src.app.handlers.message.validation import check_skip_bot_sender
from src.app.handlers.updates_filter import (
    filter_handle_edited_message,
    filter_handle_message,
)

OWN = "ai_spam_blocker_bot"


def _message(*, is_bot: bool = False, text: str = "/start") -> Message:
    return Message.model_construct(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat.model_construct(id=-100, type="supergroup", title="group"),
        from_user=User.model_construct(id=7, is_bot=is_bot, first_name="x"),
        text=text,
    )


def _mock_message(*, is_bot: bool = False, text: str = "/start") -> MagicMock:
    message = MagicMock()
    message.text = text
    message.from_user = MagicMock()
    message.from_user.is_bot = is_bot
    message.delete = AsyncMock()
    message.answer = AsyncMock()
    return message


class TestUpdatesFilterExcludesBots:
    """Сообщение другого бота не должно доходить до модерации."""

    @pytest.mark.asyncio
    async def test_new_message_from_bot_is_filtered_out(self):
        assert await filter_handle_message(_message(is_bot=True)) is False

    @pytest.mark.asyncio
    async def test_new_message_from_human_passes(self):
        assert await filter_handle_message(_message(is_bot=False)) is True

    @pytest.mark.asyncio
    async def test_edited_message_from_bot_is_filtered_out(self):
        assert await filter_handle_edited_message(_message(is_bot=True)) is False

    @pytest.mark.asyncio
    async def test_edited_message_from_human_passes(self):
        assert await filter_handle_edited_message(_message(is_bot=False)) is True

    @pytest.mark.asyncio
    async def test_anonymous_sender_still_passes_the_filter(self):
        """Контроль: `from_user=None` (анонимный админ, пост канала) не отсекается.

        `F.from_user.is_bot` на None даёт False, значит `~` пропускает — и это
        то, что нужно: такие сообщения разбирает канальная ветка, а не фильтр.
        """
        anonymous = Message.model_construct(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat.model_construct(id=-100, type="supergroup", title="group"),
            from_user=None,
            text="/start",
        )
        assert await filter_handle_message(anonymous) is True
        assert await filter_handle_edited_message(anonymous) is True


class TestCheckSkipBotSender:
    """Второй рубеж внутри пайплайна — на случай, если фильтр обойдён."""

    def test_bot_sender_is_skipped(self):
        skip, reason = check_skip_bot_sender(_message(is_bot=True))
        assert skip is True
        assert reason == "message_from_bot_skipped"

    def test_human_sender_is_not_skipped(self):
        skip, reason = check_skip_bot_sender(_message(is_bot=False))
        assert skip is False
        assert reason == ""

    def test_anonymous_sender_is_not_skipped_here(self):
        """from_user=None (анонимный админ / пост канала) — решает канальная ветка."""
        message = Message.model_construct(
            message_id=1,
            date=datetime.now(UTC),
            chat=Chat.model_construct(id=-100, type="supergroup", title="group"),
            from_user=None,
            text="/start",
        )
        skip, reason = check_skip_bot_sender(message)
        assert skip is False
        assert reason == ""


class TestGroupCommandAddressee:
    """`delete_and_redirect_to_pm` уважает адресата команды."""

    @pytest.mark.asyncio
    async def test_command_from_other_bot_is_ignored(self):
        message = _mock_message(is_bot=True, text="/respond")

        with patch(
            "src.app.handlers.command_handlers.get_bot_username", return_value=OWN
        ):
            result = await delete_and_redirect_to_pm(message)

        assert result == "command_from_bot_skipped"
        message.delete.assert_not_awaited()
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_command_addressed_to_other_bot_is_ignored(self):
        message = _mock_message(is_bot=False, text="/respond@OtherBot")

        with patch(
            "src.app.handlers.command_handlers.get_bot_username", return_value=OWN
        ):
            result = await delete_and_redirect_to_pm(message)

        assert result == "command_addressed_to_other_bot_skipped"
        message.delete.assert_not_awaited()
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_command_addressed_to_us_is_handled(self):
        """Контроль: команда с нашим @username по-прежнему удаляется и получает ответ."""
        message = _mock_message(is_bot=False, text=f"/respond@{OWN}")

        with patch(
            "src.app.handlers.command_handlers.get_bot_username", return_value=OWN
        ):
            result = await delete_and_redirect_to_pm(message)

        assert result == "command_group_redirect_to_pm"
        message.delete.assert_awaited_once()
        message.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_command_without_addressee_is_handled(self):
        """Контроль: обычная команда без @username — наша."""
        message = _mock_message(is_bot=False, text="/help")

        with patch(
            "src.app.handlers.command_handlers.get_bot_username", return_value=OWN
        ):
            result = await delete_and_redirect_to_pm(message)

        assert result == "command_group_redirect_to_pm"
        message.delete.assert_awaited_once()
        message.answer.assert_awaited_once()
