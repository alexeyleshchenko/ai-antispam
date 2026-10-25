"""#123 — гость-бот: полезная нагрузка модерируется от имени вызвавшего.

Три поверхности:
1. `determine_effective_user_id` — effective id для гостевого бота это
   ВЫЗВАВШИЙ человек, а не одноразовый бот (шаг 2 плана);
2. `MessageNotificationContext.from_message` — имя/username нарушителя в
   уведомлении тоже вызвавший, иначе кнопка банит человека, а админ читает
   имя бота;
3. `extract_update_type_ignored` — пропущенный бот называется своим тегом, а
   не `message_ignored` (шаг 3).
"""

import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import Chat, Message, User

from src.app.common.utils import determine_effective_user_id
from src.app.handlers.message.pipeline import handle_moderated_message
from src.app.main import extract_update_type_ignored
from src.app.types import MessageNotificationContext

SUMMONER_ID = 6350489404
BOT_ID = 8839849835
GROUP_ID = -1001503592176


def _message(
    *,
    from_user: User | None = None,
    sender_chat: Chat | None = None,
    caller: User | None = None,
) -> Message:
    return Message.model_construct(
        message_id=17836,
        date=datetime.now(UTC),
        chat=Chat.model_construct(id=GROUP_ID, type="supergroup", title="chat"),
        from_user=from_user,
        sender_chat=sender_chat,
        guest_bot_caller_user=caller,
        text="Обмен валют по выгодному курсу",
    )


def _guest_bot(caller: User | None) -> Message:
    return _message(
        from_user=User.model_construct(id=BOT_ID, is_bot=True, first_name="Rates"),
        caller=caller,
    )


class TestEffectiveUserId:
    """Кого именно модерируем."""

    def test_guest_bot_attributes_to_summoner(self):
        message = _guest_bot(
            User.model_construct(
                id=SUMMONER_ID, is_bot=False, first_name="Alex", username="alex"
            )
        )
        assert determine_effective_user_id(message) == SUMMONER_ID

    def test_ordinary_bot_keeps_its_own_id(self):
        message = _message(
            from_user=User.model_construct(id=BOT_ID, is_bot=True, first_name="Other")
        )
        assert determine_effective_user_id(message) == BOT_ID

    def test_human_keeps_own_id(self):
        message = _message(
            from_user=User.model_construct(id=42, is_bot=False, first_name="Human")
        )
        assert determine_effective_user_id(message) == 42

    def test_channel_post_keeps_channel_id(self):
        message = _message(
            sender_chat=Chat.model_construct(id=-100999, type="channel", title="Chan")
        )
        assert determine_effective_user_id(message) == -100999

    def test_anonymous_admin_keeps_group_id_not_channel(self):
        # sender_chat == chat: анонимный админ, effective id берётся из from_user.
        message = _message(
            sender_chat=Chat.model_construct(id=GROUP_ID, type="supergroup"),
            from_user=User.model_construct(id=7, is_bot=False, first_name="Anon"),
        )
        assert determine_effective_user_id(message) == 7

    def test_senderless_message_has_no_effective_id(self):
        assert determine_effective_user_id(_message()) is None


class TestNotificationContext:
    """Уведомление админу должно называть человека, а не бота."""

    def test_guest_bot_notification_names_the_summoner(self):
        message = _guest_bot(
            User.model_construct(
                id=SUMMONER_ID,
                is_bot=False,
                first_name="Alex",
                last_name="L",
                username="alex",
            )
        )
        context = MessageNotificationContext.from_message(message)
        assert context.effective_user_id == SUMMONER_ID
        assert context.violator_name == "Alex L"
        assert context.violator_username == "alex"

    def test_ordinary_bot_notification_names_the_bot(self):
        message = _message(
            from_user=User.model_construct(
                id=BOT_ID, is_bot=True, first_name="Rates", username="rates_bot"
            )
        )
        context = MessageNotificationContext.from_message(message)
        assert context.effective_user_id == BOT_ID
        assert context.violator_username == "rates_bot"


class TestIgnoredTag:
    """Пропущенный чужой бот называется своим именем, а не `message_ignored`."""

    def test_extract_update_type_ignored_message_from_bot(self):
        update = {
            "update_id": 726166377,
            "message": {"message_id": 17836, "from": {"id": BOT_ID, "is_bot": True}},
        }
        assert extract_update_type_ignored(update) == "message_foreign_bot_ignored"

    def test_extract_update_type_ignored_edited_message_from_bot(self):
        update = {
            "update_id": 726166378,
            "edited_message": {
                "message_id": 17836,
                "from": {"id": BOT_ID, "is_bot": True},
            },
        }
        assert (
            extract_update_type_ignored(update) == "edited_message_foreign_bot_ignored"
        )

    def test_extract_update_type_ignored_message_from_human(self):
        update = {
            "update_id": 726166379,
            "message": {"message_id": 1, "from": {"id": 42, "is_bot": False}},
        }
        assert extract_update_type_ignored(update) == "message_ignored"

    def test_extract_update_type_ignored_message_without_from(self):
        # Контроль регрессии: tests/test_server.py шлёт апдейт без `from`.
        update = {"update_id": 726166380, "message": {"message_id": 2}}
        assert extract_update_type_ignored(update) == "message_ignored"

    def test_extract_update_type_ignored_callback_from_bot(self):
        update = {
            "update_id": 726166381,
            "callback_query": {"id": "1", "from": {"id": BOT_ID, "is_bot": True}},
        }
        assert extract_update_type_ignored(update) == "callback_query_ignored"


class TestEarlyExitLogs:
    """Выходы, которые раньше молчали, теперь оставляют строку (#123)."""

    @staticmethod
    def _exit_lines(caplog) -> list[str]:
        return [
            r.getMessage()
            for r in caplog.records
            if "Moderation early exit" in r.getMessage()
        ]

    @pytest.mark.asyncio
    async def test_early_exit_admin_skip_is_logged_at_debug(self, caplog):
        message = _message(
            from_user=User.model_construct(id=42, is_bot=False, first_name="Admin")
        )
        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "src.app.handlers.message.pipeline.is_member_in_group",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "src.app.handlers.message.pipeline.validate_group_and_check_early_exits",
                new=AsyncMock(return_value=(MagicMock(), "message_from_admin_skipped")),
            ),
        ):
            reason = await handle_moderated_message(message)

        assert reason == "message_from_admin_skipped"
        records = [
            r
            for r in caplog.records
            if "Moderation early exit" in r.getMessage()
        ]
        assert len(records) == 1
        record = records[0]
        assert record.levelno == logging.DEBUG
        text = record.getMessage()
        assert "message_from_admin_skipped" in text
        assert f"chat_id={GROUP_ID}" in text
        assert "message_id=17836" in text

    @pytest.mark.asyncio
    async def test_early_exit_no_user_info_is_logged_at_warning(self, caplog):
        # Ни from_user, ни sender_chat: effective id не определяется.
        message = _message()
        with caplog.at_level(logging.DEBUG):
            reason = await handle_moderated_message(message)

        assert reason == "message_no_user_info"
        records = [
            r
            for r in caplog.records
            if "Moderation early exit" in r.getMessage()
        ]
        assert len(records) == 1
        record = records[0]
        assert record.levelno == logging.WARNING
        text = record.getMessage()
        assert "message_no_user_info" in text
        assert f"chat_id={GROUP_ID}" in text
        assert "message_id=17836" in text

    @pytest.mark.asyncio
    async def test_early_exit_absent_for_a_message_that_is_not_an_admin_skip(
        self, caplog
    ):
        message = _message(
            from_user=User.model_construct(id=42, is_bot=False, first_name="Human")
        )
        with (
            caplog.at_level(logging.DEBUG),
            patch(
                "src.app.handlers.message.pipeline.is_member_in_group",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "src.app.handlers.message.pipeline.validate_group_and_check_early_exits",
                new=AsyncMock(return_value=(None, "message_trusted_member_skipped")),
            ),
            patch(
                "src.app.handlers.message.pipeline._handle_trusted_member_exit",
                new=AsyncMock(),
            ),
        ):
            reason = await handle_moderated_message(message)

        assert reason == "message_trusted_member_skipped"
        assert self._exit_lines(caplog) == []
