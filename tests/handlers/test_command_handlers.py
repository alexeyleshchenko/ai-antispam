"""Unit tests for /start and /help command handlers (no integration)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.database.models import ModerationMode
from src.app.handlers.command_handlers import (
    handle_help_command,
    handle_stats_command,
)
from src.app.types import ContextStatus


def _make_start_message():
    message = MagicMock()
    message.text = "/start"
    message.chat.type = "private"
    user = MagicMock()
    user.id = 12345
    user.username = "testuser"
    message.from_user = user
    message.reply = AsyncMock()
    message.answer = AsyncMock()
    return message


class TestStartCommandNewUser:
    """Test /start handler for new users (welcome message and linked channel offer)."""

    @pytest.mark.asyncio
    async def test_sends_welcome_without_offer_when_no_linked_channel(self):
        message = _make_start_message()
        mock_chat = MagicMock()
        mock_chat.personal_chat = None
        mock_chat.bio = None

        with (
            patch(
                "src.app.handlers.command_handlers.initialize_new_admin",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.handlers.command_handlers.update_admin_username_if_needed",
                new_callable=AsyncMock,
            ),
            patch(
                "src.app.handlers.command_handlers.get_admin",
                new_callable=AsyncMock,
                return_value=MagicMock(language_code="en"),
            ),
            patch(
                "src.app.handlers.command_handlers.collect_user_context",
                new_callable=AsyncMock,
            ) as mock_collect,
            patch(
                "src.app.handlers.command_handlers.bot",
            ) as mock_bot,
        ):
            mock_collect.return_value = MagicMock(
                linked_channel=MagicMock(
                    status=ContextStatus.EMPTY,
                    content=None,
                )
            )
            mock_bot.get_chat = AsyncMock(return_value=mock_chat)

            result = await handle_help_command(message)

        assert result == "command_start_new_user_sent"
        message.reply.assert_awaited_once()
        sent_text = message.reply.call_args[0][0]
        call_kw = message.reply.call_args[1]
        assert call_kw["parse_mode"] == "HTML"
        assert "Welcome" in sent_text
        message.answer.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_welcome_with_offer_when_linked_channel_found(self):
        message = _make_start_message()
        channel_id = -1001234567890
        mock_chat = MagicMock()
        mock_chat.title = "My Channel"
        mock_chat.username = "mychannel"
        mock_chat.id = channel_id
        mock_chat.personal_chat = None
        mock_chat.bio = None

        with (
            patch(
                "src.app.handlers.command_handlers.initialize_new_admin",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.handlers.command_handlers.update_admin_username_if_needed",
                new_callable=AsyncMock,
            ),
            patch(
                "src.app.handlers.command_handlers.get_admin",
                new_callable=AsyncMock,
                return_value=MagicMock(language_code="en"),
            ),
            patch(
                "src.app.handlers.command_handlers.collect_user_context",
                new_callable=AsyncMock,
            ) as mock_collect,
            patch(
                "src.app.handlers.command_handlers.bot",
            ) as mock_bot,
        ):
            mock_summary = MagicMock()
            mock_summary.channel_id = channel_id
            mock_collect.return_value = MagicMock(
                linked_channel=MagicMock(
                    status=ContextStatus.FOUND,
                    content=mock_summary,
                )
            )
            mock_bot.get_chat = AsyncMock(return_value=mock_chat)

            result = await handle_help_command(message)

        assert result == "command_start_new_user_sent"
        # First message: welcome
        message.reply.assert_awaited_once()
        sent_welcome = message.reply.call_args[0][0]
        assert "Welcome" in sent_welcome

        # Second message: offer
        message.answer.assert_awaited_once()
        offer_text = message.answer.call_args[0][0]
        call_kw = message.answer.call_args[1]
        assert "My Channel" in offer_text
        assert "mychannel" in offer_text
        assert call_kw["reply_markup"] is not None

    @pytest.mark.asyncio
    async def test_sends_base_welcome_on_collect_error(self):
        message = _make_start_message()

        with (
            patch(
                "src.app.handlers.command_handlers.initialize_new_admin",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "src.app.handlers.command_handlers.update_admin_username_if_needed",
                new_callable=AsyncMock,
            ),
            patch(
                "src.app.handlers.command_handlers.get_admin",
                new_callable=AsyncMock,
                return_value=MagicMock(language_code="en"),
            ),
            patch(
                "src.app.handlers.command_handlers.collect_user_context",
                new_callable=AsyncMock,
                side_effect=Exception("MTProto error"),
            ),
            patch(
                "src.app.handlers.command_handlers.bot",
            ) as mock_bot,
        ):
            mock_bot.get_chat = AsyncMock(side_effect=Exception("API error"))

            result = await handle_help_command(message)

        assert result == "command_start_new_user_sent"
        message.reply.assert_awaited_once()
        sent_text = message.reply.call_args[0][0]
        assert "Welcome" in sent_text
        message.answer.assert_not_called()


def _stats_message():
    message = MagicMock()
    message.chat.type = "private"
    user = MagicMock()
    user.id = 12345
    message.from_user = user
    message.reply = AsyncMock()
    return message


class TestStatsCommandRendering:
    """/stats rendering: localized topic age, no 'old', blank line between groups."""

    @pytest.mark.asyncio
    async def test_groups_separated_by_blank_line_with_localized_age(self):
        message = _stats_message()
        three_days_ago = datetime.now(UTC) - timedelta(days=3)
        admin_stats = {
            "global": {
                "processed": 55,
                "spam": 53,
                "approved": 520,
                "spam_examples": 142,
            },
            "groups": [
                {
                    "title": "Realty Chat",
                    "is_moderation_enabled": True,
                    "approved_users_count": 520,
                    "stats": {"processed": 55, "spam": 53},
                    "topic_description_short": "Real estate deal case studies",
                    "topic_updated_at": three_days_ago,
                },
                {
                    "title": "PHP Jobs",
                    "is_moderation_enabled": False,
                    "approved_users_count": 1,
                    "stats": {"processed": 0, "spam": 0},
                    "topic_description_short": "PHP freelancing",
                    "topic_updated_at": None,
                },
            ],
        }
        with (
            patch(
                "src.app.handlers.command_handlers.get_admin",
                AsyncMock(return_value=MagicMock(language_code="en")),
            ),
            patch(
                "src.app.handlers.command_handlers.get_admin_credits",
                AsyncMock(return_value=667),
            ),
            patch(
                "src.app.handlers.command_handlers.get_spent_credits_last_week",
                AsyncMock(return_value=57),
            ),
            patch(
                "src.app.handlers.command_handlers.get_admin_stats",
                AsyncMock(return_value=admin_stats),
            ),
            patch(
                "src.app.handlers.command_handlers.get_moderation_mode",
                AsyncMock(return_value=ModerationMode.DELETE),
            ),
        ):
            result = await handle_stats_command(message)

        assert result == "command_stats_sent"
        text = message.reply.call_args.args[0]

        # Localized age, no hardcoded " old" suffix.
        assert "Real estate deal case studies · 3d ago" in text
        assert "old" not in text

        # Exactly one blank line separates the two group blocks.
        first_block = (
            "✅ <b>Realty Chat</b>\n"
            "   └ 📨 55 | 🗑 53 | 👤 520 │ Real estate deal case studies · 3d ago"
        )
        assert f"{first_block}\n\n❌ <b>PHP Jobs</b>" in text
        assert "PHP freelancing" in text
