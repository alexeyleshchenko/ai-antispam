"""Tests for status_handlers: Telegram auto-add detection + awaiting-rights upsert."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberUpdated,
    User,
)

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


def _real_event(
    *,
    chat_type: str = "supergroup",
    from_id: int = HUMAN_ID,
    old_status: str = "member",
    new_status: str = "administrator",
    chat_id: int = DISCUSSION_ID,
    can_delete: bool = True,
    can_restrict: bool = True,
) -> ChatMemberUpdated:
    """Build a REAL ChatMemberUpdated (logfire extract_args needs real models)."""
    chat = Chat(id=chat_id, type=chat_type, title="Discussion")
    from_user = User(id=from_id, is_bot=False, first_name="Admin")
    if new_status == "administrator":
        new_member = ChatMemberAdministrator(
            user=User(id=BOT_ID, is_bot=True, first_name="Bot"),
            status="administrator",
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=can_delete,
            can_manage_video_chats=False,
            can_restrict_members=can_restrict,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=True,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
        )
    else:
        new_member = ChatMemberMember(
            user=User(id=BOT_ID, is_bot=True, first_name="Bot"),
            status="member",
        )
    if old_status == "administrator":
        old_member = ChatMemberAdministrator(
            user=User(id=BOT_ID, is_bot=True, first_name="Bot"),
            status="administrator",
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=can_delete,
            can_manage_video_chats=False,
            can_restrict_members=can_restrict,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=True,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
        )
    else:
        old_member = ChatMemberMember(
            user=User(id=BOT_ID, is_bot=True, first_name="Bot"),
            status="member",
        )
    return ChatMemberUpdated(
        chat=chat,
        from_user=from_user,
        date=1786114347,
        old_chat_member=old_member,
        new_chat_member=new_member,
    )


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


class TestActivateDiscussionGroup:
    """Lifecycle: awaiting-rights row flips active on human admin promotion."""

    @pytest.mark.asyncio
    @patch("src.app.handlers.status_handlers.activate_discussion_group")
    @patch("src.app.handlers.status_handlers.update_group_admins")
    @patch("src.app.handlers.status_handlers._send_promo_message")
    async def test_promotion_activates_awaiting_group(
        self, mock_promo, mock_update, mock_activate
    ):
        """Human promotes bot to admin -> activation called with the group id."""
        # member -> administrator with full rights, from a HUMAN (real models —
        # logfire extract_args needs them)
        event = _real_event(
            from_id=HUMAN_ID,
            old_status="member",
            new_status="administrator",
            chat_id=DISCUSSION_ID,
        )
        mock_activate.return_value = True

        with (
            patch(
                "src.app.handlers.status_handlers.set_no_rights_detected_at",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers._notify_admins_about_rights",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers._resolve_lang",
                AsyncMock(return_value="en"),
            ),
            patch(
                "src.app.handlers.status_handlers.format_chat_or_channel_display",
                return_value="Discussion",
            ),
            patch(
                "src.app.handlers.status_handlers.retry_on_network_error",
                side_effect=lambda f: f,
            ),
            patch(
                "src.app.handlers.status_handlers.bot.send_message",
                AsyncMock(),
            ),
        ):
            from src.app.handlers.status_handlers import _handle_bot_added

            await _handle_bot_added(
                event, DISCUSSION_ID, HUMAN_ID, "Discussion", "administrator"
            )

        mock_activate.assert_awaited_once_with(DISCUSSION_ID)

    @pytest.mark.asyncio
    @patch("src.app.handlers.status_handlers.activate_discussion_group")
    @patch("src.app.handlers.status_handlers.update_group_admins")
    @patch("src.app.handlers.status_handlers._send_promo_message")
    async def test_no_rights_no_activation(
        self, mock_promo, mock_update, mock_activate
    ):
        """Bot added as plain member (no admin rights) -> no activation call."""
        event = _real_event(
            from_id=HUMAN_ID,
            old_status="left",
            new_status="member",
            chat_id=DISCUSSION_ID,
        )
        mock_activate.return_value = True

        with (
            patch(
                "src.app.handlers.status_handlers.set_no_rights_detected_at",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers._notify_admins_about_rights",
                AsyncMock(),
            ),
        ):
            from src.app.handlers.status_handlers import _handle_bot_added

            await _handle_bot_added(event, DISCUSSION_ID, HUMAN_ID, "Discussion", "member")

        mock_activate.assert_not_awaited()
        mock_promo.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("src.app.handlers.status_handlers.activate_discussion_group")
    @patch("src.app.handlers.status_handlers.update_group_admins")
    @patch("src.app.handlers.status_handlers._send_promo_message")
    async def test_activation_failure_swallowed(
        self, mock_promo, mock_update, mock_activate
    ):
        """Activation DB error must not crash the add flow (suppress)."""
        event = _real_event(
            from_id=HUMAN_ID,
            old_status="member",
            new_status="administrator",
            chat_id=DISCUSSION_ID,
        )
        mock_activate.side_effect = Exception("db down")

        with (
            patch(
                "src.app.handlers.status_handlers.set_no_rights_detected_at",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers._notify_admins_about_rights",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers._resolve_lang",
                AsyncMock(return_value="en"),
            ),
            patch(
                "src.app.handlers.status_handlers.format_chat_or_channel_display",
                return_value="Discussion",
            ),
            patch(
                "src.app.handlers.status_handlers.retry_on_network_error",
                side_effect=lambda f: f,
            ),
            patch(
                "src.app.handlers.status_handlers.bot.send_message",
                AsyncMock(),
            ),
        ):
            from src.app.handlers.status_handlers import _handle_bot_added

            # Must not raise despite the DB error
            await _handle_bot_added(
                event, DISCUSSION_ID, HUMAN_ID, "Discussion", "administrator"
            )

        mock_activate.assert_awaited_once_with(DISCUSSION_ID)


class TestFullLifecycleAwaitingRightsActivation:
    """Real-DB lifecycle: awaiting-rights upsert → promotion → active.

    This is the test that would have caught the original Critical A/D bug:
    `update_group_admins`' ON CONFLICT never flipped moderation_enabled, so a
    discussion group registered awaiting-rights stayed disabled forever after
    the owner promoted the bot to admin. Runs against the real SQLite/Postgres
    test pool — only the network (send/promo/lang) is mocked.
    """

    @pytest.mark.asyncio
    async def test_awaiting_rights_group_activates_after_promotion(
        self, patched_db_conn, clean_db
    ):
        from app.database import (
            is_moderation_enabled,
            upsert_awaiting_rights_group,
        )
        from src.app.handlers.status_handlers import _handle_bot_added

        # The DB fixtures patch app.database.postgres_connection._pool, but the
        # handler's internals (src.app.database.group_operations) import a
        # SEPARATE module instance (dual-module: app.* vs src.app.*). Sync the
        # patched pool into the src.app namespace so the real handler+DB path
        # runs against the test pool, not a live asyncpg connection.
        import app.database.postgres_connection as app_pc
        import src.app.database.postgres_connection as src_pc

        src_pc._pool = app_pc._pool
        try:
            # Stage 1: auto-add path registers the discussion group as awaiting-rights
            await upsert_awaiting_rights_group(
                DISCUSSION_ID, "Discussion", None, linked_channel_id=CHANNEL_ID
            )
            assert await is_moderation_enabled(DISCUSSION_ID) is False

            # Stage 2: owner promotes the bot to admin (real event, real handler,
            # real DB — only network calls mocked)
            event = _real_event(
                from_id=HUMAN_ID,
                old_status="member",
                new_status="administrator",
                chat_id=DISCUSSION_ID,
            )
            with (
                patch(
                    "src.app.handlers.status_handlers._send_promo_message",
                    AsyncMock(),
                ),
                patch(
                    "src.app.handlers.status_handlers._resolve_lang",
                    AsyncMock(return_value="en"),
                ),
                patch(
                    "src.app.handlers.status_handlers.format_chat_or_channel_display",
                    return_value="Discussion",
                ),
                patch(
                    "src.app.handlers.status_handlers.retry_on_network_error",
                    side_effect=lambda f: f,
                ),
                patch(
                    "src.app.handlers.status_handlers.bot.send_message",
                    AsyncMock(),
                ),
            ):
                await _handle_bot_added(
                    event, DISCUSSION_ID, HUMAN_ID, "Discussion", "administrator"
                )

            # Stage 3: the group is now active — the original bug left it disabled
            assert await is_moderation_enabled(DISCUSSION_ID) is True
        finally:
            src_pc._pool = None

    @pytest.mark.asyncio
    async def test_awaiting_rights_group_stays_disabled_without_promotion(
        self, patched_db_conn, clean_db
    ):
        """No promotion (plain member add) → still awaiting-rights (disabled)."""
        from app.database import is_moderation_enabled, upsert_awaiting_rights_group
        from src.app.handlers.status_handlers import _handle_bot_added

        import app.database.postgres_connection as app_pc
        import src.app.database.postgres_connection as src_pc

        src_pc._pool = app_pc._pool
        try:
            await upsert_awaiting_rights_group(
                DISCUSSION_ID, "Discussion", None, linked_channel_id=CHANNEL_ID
            )
            assert await is_moderation_enabled(DISCUSSION_ID) is False

            event = _real_event(
                from_id=HUMAN_ID,
                old_status="left",
                new_status="member",
                chat_id=DISCUSSION_ID,
            )
            with (
                # NOTE: update_group_admins is mocked here because the SQLite
                # test adapter converts its ON CONFLICT DO UPDATE (no RETURNING)
                # into INSERT OR REPLACE, which would wipe the awaiting-rights
                # row and reset moderation_enabled to the schema default. On real
                # Postgres the ON CONFLICT preserves the row untouched. The
                # no-activation behavior itself is covered mock-level by
                # test_no_rights_no_activation and SQL-level by
                # test_activate_discussion_group_leaves_disabled_group_alone.
                patch(
                    "src.app.handlers.status_handlers.update_group_admins",
                    AsyncMock(),
                ),
                patch(
                    "src.app.handlers.status_handlers.set_no_rights_detected_at",
                    AsyncMock(),
                ),
                patch(
                    "src.app.handlers.status_handlers._notify_admins_about_rights",
                    AsyncMock(),
                ),
            ):
                await _handle_bot_added(event, DISCUSSION_ID, HUMAN_ID, "Discussion", "member")

            # No admin rights → activation must NOT have happened
            assert await is_moderation_enabled(DISCUSSION_ID) is False
        finally:
            src_pc._pool = None
