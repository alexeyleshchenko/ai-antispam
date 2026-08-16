"""Tests for the /scan command, scan_chat: picker callback, and topic-age display.

Command flow: 0 groups -> no_groups; 1 group -> direct scan; many -> inline
keyboard with scan_chat:<id> buttons. Callback re-validates admin, runs the scan,
and reports the outcome. Age helper formats topic_updated_at.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import CallbackQuery, Chat, Message, User

from app.database import (
    get_admin_groups,  # noqa: F401 — re-exported: used for patching by name
)
from app.database.models import ModerationMode
from app.handlers.callback_handlers import (
    _reply_scan_result,
    handle_scan_chat_callback,
)
from app.handlers.command_handlers import (
    _format_topic_age,
    handle_scan_command,
    handle_stats_command,
)


def _scan_message() -> Message:
    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=42, is_bot=False, first_name="Admin")
    msg.chat = Chat(id=99, type="private")
    msg.answer = AsyncMock()
    msg.reply = AsyncMock()
    return msg


def _callback(group_id: int) -> CallbackQuery:
    cb = AsyncMock(spec=CallbackQuery)
    cb.data = f"scan_chat:{group_id}"
    cb.from_user = User(id=42, is_bot=False, first_name="Admin")
    cb.message = AsyncMock(spec=Message)
    cb.message.chat = Chat(id=100, type="private")
    cb.message.message_id = 5
    cb.message.edit_text = AsyncMock()
    cb.answer = AsyncMock()
    return cb


def _group_dict(group_id: int, title: str, topic_short=None, topic_updated=None):
    return {
        "id": group_id,
        "title": title,
        "is_moderation_enabled": True,
        "topic_description_short": topic_short,
        "topic_updated_at": topic_updated,
    }


class TestFormatTopicAge:
    def test_none_returns_none(self):
        assert _format_topic_age(None) is None

    def test_today(self):
        assert _format_topic_age(datetime.now(timezone.utc)) == "today"

    def test_three_days(self):
        old = datetime.now(timezone.utc) - timedelta(days=3)
        assert _format_topic_age(old) == "3d"

    def test_one_day(self):
        old = datetime.now(timezone.utc) - timedelta(days=1)
        assert _format_topic_age(old) == "1d"

    def test_iso_string_input(self):
        old = datetime.now(timezone.utc) - timedelta(days=5)
        assert _format_topic_age(old.isoformat()) == "5d"

    def test_garbage_string(self):
        assert _format_topic_age("not-a-date") is None


class TestScanCommand:
    @pytest.mark.asyncio
    async def test_no_groups(self):
        msg = _scan_message()
        with patch(
            "app.handlers.command_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.command_handlers.get_admin_groups",
            AsyncMock(return_value=[]),
        ):
            result = await handle_scan_command(msg)

        assert result == "command_scan_no_groups"
        msg.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_group_direct_scan(self):
        msg = _scan_message()
        groups = [_group_dict(-100123, "PHP Jobs")]
        with patch(
            "app.handlers.command_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.command_handlers.get_admin_groups",
            AsyncMock(return_value=groups),
        ), patch(
            "app.spam.chat_topics.scan_chat_topics",
            AsyncMock(
                return_value=MagicMock(status="ok", detail="PHP jobs")
            ),
        ) as mock_scan:
            result = await handle_scan_command(msg)

        assert result == "command_scan_sent"
        mock_scan.assert_awaited_once_with(-100123)
        # Two messages: starting + result
        assert msg.answer.await_count == 2

    @pytest.mark.asyncio
    async def test_multiple_groups_show_picker(self):
        msg = _scan_message()
        groups = [
            _group_dict(-100123, "PHP Jobs"),
            _group_dict(-100456, "Freelance"),
        ]
        with patch(
            "app.handlers.command_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.command_handlers.get_admin_groups",
            AsyncMock(return_value=groups),
        ):
            await handle_scan_command(msg)

        kwargs = msg.answer.call_args.kwargs
        keyboard = kwargs.get("reply_markup")
        assert keyboard is not None
        buttons = keyboard.inline_keyboard
        assert len(buttons) == 2
        # Each button carries scan_chat:<id> callback data
        assert buttons[0][0].callback_data == "scan_chat:-100123"
        assert buttons[1][0].callback_data == "scan_chat:-100456"


class TestScanCallback:
    @pytest.mark.asyncio
    async def test_non_admin_refused(self):
        cb = _callback(-100123)
        with patch(
            "app.handlers.callback_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.callback_handlers.get_admin_groups",
            AsyncMock(return_value=[]),  # caller not admin of the group
        ):
            result = await handle_scan_chat_callback(cb)

        assert result == "callback_scan_not_admin"
        cb.answer.assert_awaited_once()
        # show_alert=True for refusal
        assert cb.answer.call_args.kwargs.get("show_alert") is True

    @pytest.mark.asyncio
    async def test_admin_success_reports_short_and_today_age(self):
        cb = _callback(-100123)
        groups = [
            _group_dict(
                -100123,
                "PHP Jobs",
                topic_short="PHP jobs",
                topic_updated=datetime.now(timezone.utc) - timedelta(days=3),
            )
        ]
        with patch(
            "app.handlers.callback_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.callback_handlers.get_admin_groups",
            AsyncMock(return_value=groups),
        ), patch(
            "app.spam.chat_topics.scan_chat_topics",
            AsyncMock(return_value=MagicMock(status="ok", detail="PHP jobs")),
        ):
            result = await handle_scan_chat_callback(cb)

        assert result == "callback_scan_done"
        # Edited text contains short description and the age of the JUST-written
        # scan ("today") — NOT the stale pre-scan dict value ("3d", see #review).
        edit_text = cb.message.edit_text.call_args.args[0]
        assert "PHP jobs" in edit_text
        assert "today" in edit_text
        assert "3d" not in edit_text

    @pytest.mark.asyncio
    async def test_failed_scan_reports_reason(self):
        cb = _callback(-100123)
        groups = [_group_dict(-100123, "PHP Jobs")]
        with patch(
            "app.handlers.callback_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.callback_handlers.get_admin_groups",
            AsyncMock(return_value=groups),
        ), patch(
            "app.spam.chat_topics.scan_chat_topics",
            AsyncMock(
                return_value=MagicMock(status="failed", detail="fetch_failed")
            ),
        ):
            result = await handle_scan_chat_callback(cb)

        assert result == "callback_scan_done"
        edit_text = cb.message.edit_text.call_args.args[0]
        assert "fetch_failed" in edit_text

    @pytest.mark.asyncio
    async def test_title_fallback_reported(self):
        cb = _callback(-100123)
        groups = [_group_dict(-100123, "PHP Jobs")]
        with patch(
            "app.handlers.callback_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.callback_handlers.get_admin_groups",
            AsyncMock(return_value=groups),
        ), patch(
            "app.spam.chat_topics.scan_chat_topics",
            AsyncMock(
                return_value=MagicMock(
                    status="title_fallback", detail="PHP Jobs"
                )
            ),
        ):
            result = await handle_scan_chat_callback(cb)

        assert result == "callback_scan_done"
        edit_text = cb.message.edit_text.call_args.args[0]
        assert "PHP Jobs" in edit_text

    @pytest.mark.asyncio
    async def test_scan_crash_reports_error(self):
        cb = _callback(-100123)
        groups = [_group_dict(-100123, "PHP Jobs")]
        with patch(
            "app.handlers.callback_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.callback_handlers.get_admin_groups",
            AsyncMock(return_value=groups),
        ), patch(
            "app.spam.chat_topics.scan_chat_topics",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await handle_scan_chat_callback(cb)

        assert result == "callback_scan_error"
        edit_text = cb.message.edit_text.call_args.args[0]
        assert "crashed" in edit_text


class TestReplyScanResult:
    @pytest.mark.asyncio
    async def test_edits_message_with_text(self):
        cb = _callback(-100123)
        with patch(
            "app.handlers.callback_handlers.t",
            return_value="formatted",
        ):
            await _reply_scan_result(cb, "en", "scan.success", title="T")

        cb.message.edit_text.assert_awaited_once()

class TestStatsTopicLine:
    """/stats per-group line: short topic + age when present, unchanged when NULL."""

    @staticmethod
    def _stats_groups(topic_short=None, topic_updated=None):
        return [
            {
                "title": "PHP Jobs",
                "is_moderation_enabled": True,
                "approved_users_count": 5,
                "stats": {"processed": 10, "spam": 2},
                "topic_description_short": topic_short,
                "topic_updated_at": topic_updated,
            }
        ]

    @pytest.mark.asyncio
    async def test_topic_short_and_age_appended(self):
        msg = _scan_message()
        groups = self._stats_groups(
            topic_short="PHP jobs",
            topic_updated=datetime.now(timezone.utc) - timedelta(days=3),
        )
        with patch(
            "app.handlers.command_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.command_handlers.get_admin_credits",
            AsyncMock(return_value=100),
        ), patch(
            "app.handlers.command_handlers.get_spent_credits_last_week",
            AsyncMock(return_value=0),
        ), patch(
            "app.handlers.command_handlers.get_admin_stats",
            AsyncMock(
                return_value={
                    "global": {
                        "processed": 10,
                        "spam": 2,
                        "approved": 5,
                        "spam_examples": 3,
                    },
                    "groups": groups,
                }
            ),
        ), patch(
            "app.handlers.command_handlers.get_moderation_mode",
            AsyncMock(return_value=ModerationMode.NOTIFY),
        ):
            result = await handle_stats_command(msg)

        assert result == "command_stats_sent"
        sent = msg.reply.call_args.args[0]
        assert "PHP jobs" in sent
        assert "3d old" in sent

    @pytest.mark.asyncio
    async def test_no_topic_leaves_line_unchanged(self):
        msg = _scan_message()
        groups = self._stats_groups()  # no topic fields
        with patch(
            "app.handlers.command_handlers.get_admin",
            AsyncMock(return_value=MagicMock(language_code="en")),
        ), patch(
            "app.handlers.command_handlers.get_admin_credits",
            AsyncMock(return_value=100),
        ), patch(
            "app.handlers.command_handlers.get_spent_credits_last_week",
            AsyncMock(return_value=0),
        ), patch(
            "app.handlers.command_handlers.get_admin_stats",
            AsyncMock(
                return_value={
                    "global": {
                        "processed": 10,
                        "spam": 2,
                        "approved": 5,
                        "spam_examples": 3,
                    },
                    "groups": groups,
                }
            ),
        ), patch(
            "app.handlers.command_handlers.get_moderation_mode",
            AsyncMock(return_value=ModerationMode.NOTIFY),
        ):
            result = await handle_stats_command(msg)

        assert result == "command_stats_sent"
        sent = msg.reply.call_args.args[0]
        assert "PHP Jobs" in sent
        assert " │ " not in sent
        assert "old" not in sent
