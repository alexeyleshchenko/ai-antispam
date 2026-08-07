"""Tests for status_handlers: Telegram auto-add detection + awaiting-rights upsert."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import ChatMemberUpdated

from src.app.handlers.status_handlers import (
    _handle_auto_added_discussion,
    _is_auto_add_discussion_update,
    handle_bot_status_update,
)

BOT_ID = 7574715711
HUMAN_ID = 123456789
DISCUSSION_ID = -1001800163907  # linked discussion supergroup (Valeri trace shape)
CHANNEL_ID = -1004352022427


def _user(uid: int) -> MagicMock:
    u = MagicMock()
    u.id = uid
    return u


def _member(status: str) -> MagicMock:
    m = MagicMock()
    m.status = status
    return m


def _event(
    *,
    chat_type: str = "supergroup",
    from_id: int = BOT_ID,
    old_status: str = "left",
    new_status: str = "member",
    chat_id: int = DISCUSSION_ID,
) -> ChatMemberUpdated:
    event = MagicMock(spec=ChatMemberUpdated)
    event.chat = MagicMock()
    event.chat.id = chat_id
    event.chat.type = chat_type
    event.chat.title = "Discussion"
    event.chat.username = None
    event.from_user = _user(from_id)
    event.old_chat_member = _member(old_status)
    event.new_chat_member = _member(new_status)
    return event


class TestIsAutoAddDiscussionUpdate:
    """Detection: supergroup + actor==bot + left→member transition."""

    async def _detect(self, event) -> bool:
        with patch(
            "src.app.handlers.status_handlers._get_bot_id",
            AsyncMock(return_value=BOT_ID),
        ):
            return await _is_auto_add_discussion_update(event)

    @pytest.mark.asyncio
    async def test_auto_add_detected(self):
        """Bot itself acts on a supergroup left→member = auto-add."""
        assert await self._detect(_event()) is True

    @pytest.mark.asyncio
    async def test_human_add_not_detected(self):
        """A human adding the bot to a supergroup is NOT an auto-add."""
        assert await self._detect(_event(from_id=HUMAN_ID)) is False

    @pytest.mark.asyncio
    async def test_channel_add_not_detected(self):
        """A channel (not supergroup) add is never auto-add."""
        assert await self._detect(_event(chat_type="channel", chat_id=CHANNEL_ID)) is False

    @pytest.mark.asyncio
    async def test_plain_supergroup_add_not_detected(self):
        """Human adds bot to plain supergroup — not auto-add."""
        assert await self._detect(_event(from_id=HUMAN_ID)) is False

    @pytest.mark.asyncio
    async def test_non_left_transition_not_detected(self):
        """member→administrator (promotion) is not an auto-add."""
        assert (
            await self._detect(_event(old_status="member", new_status="administrator"))
            is False
        )

    @pytest.mark.asyncio
    async def test_removal_not_detected(self):
        """member→left (bot removed) is not an auto-add."""
        assert await self._detect(_event(old_status="member", new_status="left")) is False


class TestHandleAutoAddedDiscussion:
    """Auto-add handler upserts awaiting-rights group, never destructive."""

    @pytest.mark.asyncio
    @patch("src.app.handlers.status_handlers.upsert_awaiting_rights_group")
    async def test_registers_awaiting_rights(self, mock_upsert):
        """Upsert called with discussion group id/title/username."""
        event = _event(chat_id=DISCUSSION_ID)
        await _handle_auto_added_discussion(event, DISCUSSION_ID, "Discussion")
        mock_upsert.assert_awaited_once_with(DISCUSSION_ID, "Discussion", None)

    @pytest.mark.asyncio
    @patch("src.app.handlers.status_handlers.upsert_awaiting_rights_group")
    async def test_no_cleanup_helpers_called(self, mock_upsert):
        """The destructive no-rights DM path must never run for auto-add."""
        with (
            patch(
                "src.app.handlers.status_handlers._notify_admins_about_rights",
                AsyncMock(side_effect=AssertionError("must not DM")),
            ),
            patch(
                "src.app.handlers.status_handlers.set_no_rights_detected_at",
                AsyncMock(side_effect=AssertionError("must not set no-rights")),
            ),
        ):
            event = _event(chat_id=DISCUSSION_ID)
            await _handle_auto_added_discussion(event, DISCUSSION_ID, "Discussion")
            mock_upsert.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("src.app.handlers.status_handlers.upsert_awaiting_rights_group")
    async def test_idempotent_double_call(self, mock_upsert):
        """Calling twice (auto-add before/after channel handler) is safe."""
        event = _event(chat_id=DISCUSSION_ID)
        await _handle_auto_added_discussion(event, DISCUSSION_ID, "Discussion")
        await _handle_auto_added_discussion(event, DISCUSSION_ID, "Discussion")
        assert mock_upsert.await_count == 2


class TestHandleBotStatusUpdateAutoAdd:
    """Integration: handle_bot_status_update routes auto-add correctly."""

    @pytest.mark.asyncio
    async def test_auto_add_routed_before_generic_path(self):
        """Auto-add returns the dedicated tag and never hits the add path."""
        event = _event(chat_id=DISCUSSION_ID)
        with (
            patch(
                "src.app.handlers.status_handlers._get_bot_id",
                AsyncMock(return_value=BOT_ID),
            ),
            patch(
                "src.app.handlers.status_handlers._handle_auto_added_discussion",
                AsyncMock(),
            ) as mock_handle,
            patch(
                "src.app.handlers.status_handlers._handle_bot_added",
                AsyncMock(side_effect=AssertionError("generic add must not run")),
            ),
            patch(
                "src.app.handlers.status_handlers._handle_permission_update",
                AsyncMock(side_effect=AssertionError("permission path must not run")),
            ),
        ):
            result = await handle_bot_status_update(event)
            assert result == "bot_auto_added_discussion"
            mock_handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_human_add_routes_to_generic_path(self):
        """A human add still goes through the normal onboarding."""
        event = _event(from_id=HUMAN_ID)
        with (
            patch(
                "src.app.handlers.status_handlers._get_bot_id",
                AsyncMock(return_value=BOT_ID),
            ),
            patch(
                "src.app.handlers.status_handlers._handle_auto_added_discussion",
                AsyncMock(side_effect=AssertionError("auto-add must not run")),
            ),
            patch(
                "src.app.handlers.status_handlers._handle_bot_added",
                AsyncMock(),
            ) as mock_added,
        ):
            result = await handle_bot_status_update(event)
            mock_added.assert_awaited_once()
            assert result == "bot_added_group"
