"""#122 — в группе с несколькими ботами не реагируем на чужие команды.

Три входа:
1. `updates_filter` — сообщение ЧУЖОГО бота не попадает в пайплайн модерации.
   Трафик бот-в-бота Bot API отбрасывает вовсе (core.telegram.org/bots/faq,
   "regardless of mode"), но это не значит, что боты нам не приходят: ответ
   гостевого бота, которого позвал человек, доставляется как user-initiated и
   несёт `guest_bot_caller_user`. Такой бот — не чужой, его сообщение
   модерируется как сообщение вызвавшего (#123), и именно этот случай здесь
   проверяется наравне с обычным ботом;
2. `delete_and_redirect_to_pm` — команда, адресованная другому боту
   (`/cmd@OtherBot`), не удаляется и не вызывает памятку;
3. та же функция — команда, адресованная НАМ, больше не удаляется и не
   порождает публичную памятку: памятка уходит в личку, чат молчит.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import Chat, Message, User

from src.app.common.guest_bots import guest_bot_caller
from src.app.handlers.command_handlers import delete_and_redirect_to_pm
from src.app.handlers.message.validation import check_skip_bot_sender
from src.app.handlers.updates_filter import (
    filter_handle_edited_message,
    filter_handle_message,
)

OWN = "ai_spam_blocker_bot"


def _message(
    *,
    is_bot: bool = False,
    text: str = "/start",
    guest_bot_caller_user: User | None = None,
) -> Message:
    """Сообщение в группе. `guest_bot_caller_user` — человек, вызвавший бота (#123)."""
    return Message.model_construct(
        message_id=1,
        date=datetime.now(UTC),
        chat=Chat.model_construct(id=-100, type="supergroup", title="group"),
        from_user=User.model_construct(id=7, is_bot=is_bot, first_name="x"),
        text=text,
        guest_bot_caller_user=guest_bot_caller_user,
    )


CALLER = User.model_construct(id=555, is_bot=False, first_name="caller")


def _mock_message(*, is_bot: bool = False, text: str = "/start") -> MagicMock:
    message = MagicMock()
    message.text = text
    message.from_user = MagicMock()
    message.from_user.id = 7
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


class TestGuestBotIsNotAForeignBot:
    """#123 — вызванный бот проходит оба рубежа, невызванный по-прежнему нет.

    Разница только в `guest_bot_caller_user`: у гостевого бота он заполнен
    Telegram-ом, у обычного — None. Один и тот же предикат
    (`common.guest_bots.is_foreign_bot_message`) стоит на фильтре роутинга и
    внутри валидации, поэтому проверяем обе двери одним набором случаев.
    """

    @pytest.mark.asyncio
    async def test_guest_bot_message_passes_both_filters(self):
        message = _message(is_bot=True, guest_bot_caller_user=CALLER)
        assert await filter_handle_message(message) is True
        assert await filter_handle_edited_message(message) is True

    @pytest.mark.asyncio
    async def test_ordinary_bot_message_is_still_filtered_out(self):
        """Контроль #122 не ослаблен: без вызвавшего бот отсекается как раньше."""
        message = _message(is_bot=True, guest_bot_caller_user=None)
        assert await filter_handle_message(message) is False
        assert await filter_handle_edited_message(message) is False

    @pytest.mark.asyncio
    async def test_human_message_is_unaffected_by_the_new_predicate(self):
        message = _message(is_bot=False)
        assert await filter_handle_message(message) is True
        assert await filter_handle_edited_message(message) is True

    def test_guest_bot_sender_is_not_skipped_here(self):
        """Второй рубеж согласен с первым: гостевого бота модерируем."""
        skip, reason = check_skip_bot_sender(
            _message(is_bot=True, guest_bot_caller_user=CALLER)
        )
        assert skip is False
        assert reason == ""

    def test_ordinary_bot_sender_is_still_skipped_here(self):
        skip, reason = check_skip_bot_sender(
            _message(is_bot=True, guest_bot_caller_user=None)
        )
        assert skip is True
        assert reason == "message_from_bot_skipped"

    def test_guest_bot_caller_returns_the_human(self):
        assert guest_bot_caller(_message(is_bot=True, guest_bot_caller_user=CALLER)) == CALLER

    def test_guest_bot_caller_is_none_for_ordinary_bot_and_human(self):
        assert guest_bot_caller(_message(is_bot=True)) is None
        assert guest_bot_caller(_message(is_bot=False)) is None


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
    async def test_command_addressed_to_us_is_answered_in_dm_only(self):
        """Своя команда: памятка уходит в ЛС, чат не трогаем (#122)."""
        message = _mock_message(is_bot=False, text=f"/respond@{OWN}")

        with (
            patch(
                "src.app.handlers.command_handlers.get_bot_username", return_value=OWN
            ),
            patch(
                "src.app.handlers.command_handlers.bot.send_message",
                new_callable=AsyncMock,
            ) as send_message,
        ):
            result = await delete_and_redirect_to_pm(message)

        assert result == "command_group_dm_sent"
        message.delete.assert_not_awaited()
        message.answer.assert_not_awaited()
        send_message.assert_awaited_once()
        assert send_message.await_args.args[0] == 7

    @pytest.mark.asyncio
    async def test_command_without_addressee_is_answered_in_dm_only(self):
        """Команда без @username — тоже только в ЛС, без публичной памятки."""
        message = _mock_message(is_bot=False, text="/help")

        with (
            patch(
                "src.app.handlers.command_handlers.get_bot_username", return_value=OWN
            ),
            patch(
                "src.app.handlers.command_handlers.bot.send_message",
                new_callable=AsyncMock,
            ) as send_message,
        ):
            result = await delete_and_redirect_to_pm(message)

        assert result == "command_group_dm_sent"
        message.delete.assert_not_awaited()
        message.answer.assert_not_awaited()
        send_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dm_refused_stays_silent(self):
        """Пользователь не начинал диалог (403) — в группе тоже молчим."""
        message = _mock_message(is_bot=False, text="/help")

        with (
            patch(
                "src.app.handlers.command_handlers.get_bot_username", return_value=OWN
            ),
            patch(
                "src.app.handlers.command_handlers.bot.send_message",
                new_callable=AsyncMock,
                side_effect=TelegramForbiddenError(
                    method=MagicMock(),
                    message="Forbidden: bot can't initiate conversation",
                ),
            ),
        ):
            result = await delete_and_redirect_to_pm(message)

        assert result == "command_group_dm_failed_silent"
        message.delete.assert_not_awaited()
        message.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_startgroup_deeplink_still_confirms_publicly(self):
        """Контроль: startgroup-диплинк — осознанное действие, ответ публичный."""
        message = _mock_message(is_bot=False, text="/start setup_123")

        with (
            patch(
                "src.app.handlers.command_handlers.get_bot_username", return_value=OWN
            ),
            patch(
                "src.app.handlers.command_handlers.bot.send_message",
                new_callable=AsyncMock,
            ) as send_message,
        ):
            result = await delete_and_redirect_to_pm(message)

        assert result == "command_group_start_param_brief"
        message.delete.assert_awaited_once()
        message.answer.assert_awaited_once()
        send_message.assert_not_awaited()
