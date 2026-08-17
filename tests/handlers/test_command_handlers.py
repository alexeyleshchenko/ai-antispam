"""Unit tests for /start and /help command handlers (no integration)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    message.bot = MagicMock()
    message.bot.send_rich_message = AsyncMock()
    return message


class TestStatsCommandRendering:
    """/stats renders a native Rich-Message report: period + per-group tables."""

    @pytest.mark.asyncio
    async def test_stats_renders_rich_tables_with_periods_and_topics(self):
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
                "src.app.handlers.command_handlers.get_spent_credits_all_time",
                AsyncMock(return_value=5433),
            ),
            patch(
                "src.app.handlers.command_handlers.get_admin_stats",
                AsyncMock(return_value=admin_stats),
            ),
        ):
            result = await handle_stats_command(message)

        assert result == "command_stats_sent"
        message.reply.assert_not_called()

        # Rich-message path: native GFM markdown for sendRichMessage.
        message.bot.send_rich_message.assert_awaited_once()
        kwargs = message.bot.send_rich_message.call_args.kwargs
        assert kwargs["chat_id"] == message.chat.id
        md = kwargs["rich_message"].markdown

        # h1 report title (balance) + h2 section headings.
        assert "# 💰 Balance: <b>667</b> stars" in md
        assert "## 📊 Statistics" in md
        assert "## 👥 By groups" in md

        # Period table: last 7 days vs all time; "—" where untracked.
        assert (
            "| Period | ⭐ Spent | 📨 Checked | 🗑 Spam | 👤 Approved | 📝 Training |"
            in md
        )
        assert "|---|---|---|---|---|---|" in md
        assert "| Last 7 days | 57 | 55 | 53 | — | — |" in md
        assert "| All time | 5433 | — | — | 520 | 142 |" in md

        # Groups table: topic as a subscript line inside the first column.
        assert "| Group | 📨 Checked (7d) | 🗑 Spam (7d) | 👤 Approved (all) |" in md
        assert (
            "| ✅ Realty Chat<br><sub>Real estate deal case studies</sub> | 55 | 53 | 520 |"
            in md
        )
        assert "| ❌ PHP Jobs<br><sub>PHP freelancing</sub> | 0 | 0 | 1 |" in md

        # Subscript legend at the end; no mode line; no age column.
        assert "<sub>ℹ️" in md
        assert "Current mode" not in md
        assert "Age" not in md
        assert "old" not in md

    @pytest.mark.asyncio
    async def test_rich_message_falls_back_to_html_when_api_rejects(self):
        """When sendRichMessage raises, /stats still answers with the HTML fallback."""
        message = _stats_message()
        message.bot.send_rich_message = AsyncMock(
            side_effect=RuntimeError("rich API unsupported")
        )
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
                "src.app.handlers.command_handlers.get_spent_credits_all_time",
                AsyncMock(return_value=5433),
            ),
            patch(
                "src.app.handlers.command_handlers.get_admin_stats",
                AsyncMock(return_value=admin_stats),
            ),
        ):
            result = await handle_stats_command(message)

        assert result == "command_stats_sent"
        message.bot.send_rich_message.assert_awaited_once()
        message.reply.assert_awaited_once()
        text = message.reply.call_args.args[0]
        # Same data as a readable HTML card, no Rich-Markdown table markup.
        assert "<b>Realty Chat</b>" in text
        assert "Real estate deal case studies" in text
        assert "Last 7 days: ⭐ 57 · 📨 55 · 🗑 53" in text
        assert "All time: ⭐ 5433 · 👤 520 · 📝 142" in text
        assert "<b>📊 Statistics</b>" in text
        assert "| Period |" not in text
        assert "Current mode" not in text
        assert "old" not in text
