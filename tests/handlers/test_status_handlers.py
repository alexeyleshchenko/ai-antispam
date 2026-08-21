"""Tests for status_handlers: Telegram auto-add detection + awaiting-rights upsert."""

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest
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
    old_can_delete: bool | None = None,
    old_can_restrict: bool | None = None,
) -> ChatMemberUpdated:
    """Build a REAL ChatMemberUpdated (logfire extract_args needs real models)."""
    chat = Chat(id=chat_id, type=chat_type, title="Discussion")
    from_user = User(id=from_id, is_bot=False, first_name="Admin")
    if old_can_delete is None:
        old_can_delete = can_delete
    if old_can_restrict is None:
        old_can_restrict = can_restrict
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
            can_delete_messages=old_can_delete,
            can_manage_video_chats=False,
            can_restrict_members=old_can_restrict,
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
        assert (
            await self._detect(_event(chat_type="channel", chat_id=CHANNEL_ID)) is False
        )

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
        assert (
            await self._detect(_event(old_status="member", new_status="left")) is False
        )


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


class TestChannelAddCompositeSettle:
    """Composite-event settle window: dedupe redeliveries, decide once, delete on completion.

    Regression for the 09:28 incident (issue: channel-add processed per-update
    racing Telegram propagation): the channel branch must settle, dedupe the same
    add's redeliveries while a decision is in flight, and delete the entry on
    completion so a later legit re-add gets a fresh decision.
    """

    def _channel_add_event(self) -> ChatMemberUpdated:
        """Channel add: human actor, left -> administrator, on the test channel."""
        return _event(
            chat_type="channel",
            from_id=HUMAN_ID,
            old_status="left",
            new_status="administrator",
            chat_id=CHANNEL_ID,
        )

    @pytest.mark.asyncio
    async def test_duplicate_add_within_window_decides_once(self):
        """Two webhook calls for the same channel-add -> decision runs exactly once."""
        event = self._channel_add_event()
        with (
            patch(
                "src.app.handlers.status_handlers._get_bot_id",
                AsyncMock(return_value=BOT_ID),
            ),
            patch(
                "src.app.handlers.status_handlers._handle_bot_added",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers.notify_channel_admins_and_leave",
                AsyncMock(),
            ) as mock_notify,
            patch(
                "src.app.handlers.status_handlers._CHANNEL_SETTLE_SECONDS",
                0.01,
            ),
        ):
            results = await asyncio.gather(
                handle_bot_status_update(event),
                handle_bot_status_update(event),
            )
        # One decision, one redelivery deduped
        assert mock_notify.await_count == 1
        assert results.count("bot_channel_add_deduped") == 1

    @pytest.mark.asyncio
    async def test_entry_deleted_after_completion_re_runs(self):
        """After the decision completes, a new call for the same add runs again."""
        event = self._channel_add_event()
        with (
            patch(
                "src.app.handlers.status_handlers._get_bot_id",
                AsyncMock(return_value=BOT_ID),
            ),
            patch(
                "src.app.handlers.status_handlers._handle_bot_added",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers.notify_channel_admins_and_leave",
                AsyncMock(),
            ) as mock_notify,
            patch(
                "src.app.handlers.status_handlers._CHANNEL_SETTLE_SECONDS",
                0.01,
            ),
        ):
            await handle_bot_status_update(event)
            await handle_bot_status_update(event)
        # Entry was deleted in `finally` -> second call is a fresh decision
        assert mock_notify.await_count == 2

    @pytest.mark.asyncio
    async def test_non_channel_add_never_touches_registry(self):
        """A supergroup add must not register a composite or settle."""
        event = _event(from_id=HUMAN_ID)  # supergroup, human add
        with (
            patch(
                "src.app.handlers.status_handlers._get_bot_id",
                AsyncMock(return_value=BOT_ID),
            ),
            patch(
                "src.app.handlers.status_handlers._handle_bot_added",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers.notify_channel_admins_and_leave",
                AsyncMock(side_effect=AssertionError("must not run for groups")),
            ),
            patch(
                "src.app.handlers.status_handlers._pending_composites",
                {},
            ) as mock_registry,
        ):
            result = await handle_bot_status_update(event)
        assert result == "bot_added_group"
        assert mock_registry == {}


class TestChannelAddGatesGroupPath:
    """Channel adds never run the group onboarding path (09:28 regression).

    The 09:28 incident: `_handle_bot_added` ran on a channel update FIRST —
    upserted the channel into `groups` via `update_group_admins`, sent promo +
    "Настройка завершена… защищаю группу test" — before the channel branch
    decided to leave. Channels must go straight to the channel decision flow.
    """

    def _channel_add_event(self) -> ChatMemberUpdated:
        """Channel add: human actor, left -> administrator, on the test channel."""
        return _event(
            chat_type="channel",
            from_id=HUMAN_ID,
            old_status="left",
            new_status="administrator",
            chat_id=CHANNEL_ID,
        )

    @pytest.mark.asyncio
    async def test_channel_add_skips_group_path(self):
        """Channel add -> group onboarding + groups upsert never run; channel flow once."""
        event = self._channel_add_event()
        with (
            patch(
                "src.app.handlers.status_handlers._get_bot_id",
                AsyncMock(return_value=BOT_ID),
            ),
            patch(
                "src.app.handlers.status_handlers._handle_bot_added",
                AsyncMock(
                    side_effect=AssertionError(
                        "group add path must not run for channel"
                    )
                ),
            ),
            patch(
                "src.app.handlers.status_handlers.update_group_admins",
                AsyncMock(side_effect=AssertionError("no groups upsert for channel")),
            ),
            patch(
                "src.app.handlers.status_handlers._send_promo_message",
                AsyncMock(side_effect=AssertionError("no promo for channel")),
            ),
            patch(
                "src.app.handlers.status_handlers.bot.send_message",
                AsyncMock(side_effect=AssertionError("no setup_done for channel")),
            ),
            patch(
                "src.app.handlers.status_handlers.notify_channel_admins_and_leave",
                AsyncMock(),
            ) as mock_notify,
            patch(
                "src.app.handlers.status_handlers._CHANNEL_SETTLE_SECONDS",
                0.01,
            ),
        ):
            result = await handle_bot_status_update(event)
        mock_notify.assert_awaited_once()
        assert result == "bot_status_updated"

    @pytest.mark.asyncio
    async def test_channel_permission_update_no_promo(self):
        """A rights change on a channel must not fire promo/setup_done (R7)."""
        # Real models — logfire extract_args and the isinstance gate in
        # _handle_permission_update need them; a MagicMock would silently pass
        # the old isinstance gate and miss the bug. Rights go False->True, so
        # WITHOUT the channel gate the promo+setup_done path WOULD fire.
        event = _real_event(
            chat_type="channel",
            from_id=HUMAN_ID,
            old_status="administrator",
            new_status="administrator",
            chat_id=CHANNEL_ID,
            can_delete=True,
            can_restrict=True,
            old_can_delete=False,
            old_can_restrict=False,
        )
        with (
            patch(
                "src.app.handlers.status_handlers._get_bot_id",
                AsyncMock(return_value=BOT_ID),
            ),
            patch(
                "src.app.handlers.status_handlers._send_promo_message",
                AsyncMock(side_effect=AssertionError("no promo for channel")),
            ),
            patch(
                "src.app.handlers.status_handlers._notify_admins_about_rights",
                AsyncMock(side_effect=AssertionError("no rights DM for channel")),
            ),
            patch(
                "src.app.handlers.status_handlers.set_no_rights_detected_at",
                AsyncMock(side_effect=AssertionError("no no-rights flow for channel")),
            ),
            patch(
                "src.app.handlers.status_handlers.clear_no_rights_detected_at",
                AsyncMock(
                    side_effect=AssertionError("no clear-rights flow for channel")
                ),
            ),
            patch(
                "src.app.handlers.status_handlers.bot.send_message",
                AsyncMock(side_effect=AssertionError("no setup_done for channel")),
            ),
        ):
            result = await handle_bot_status_update(event)
        assert result == "bot_permissions_updated"


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
                "src.app.handlers.status_handlers.clear_no_rights_detected_at",
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

            await _handle_bot_added(
                event, DISCUSSION_ID, HUMAN_ID, "Discussion", "member"
            )

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
                "src.app.handlers.status_handlers.clear_no_rights_detected_at",
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
        # The DB fixtures patch app.database.postgres_connection._pool, but the
        # handler's internals (src.app.database.group_operations) import a
        # SEPARATE module instance (dual-module: app.* vs src.app.*). Sync the
        # patched pool into the src.app namespace so the real handler+DB path
        # runs against the test pool, not a live asyncpg connection.
        import app.database.postgres_connection as app_pc
        import src.app.database.postgres_connection as src_pc
        from app.database import (
            is_moderation_enabled,
            upsert_awaiting_rights_group,
        )
        from src.app.handlers.status_handlers import _handle_bot_added

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
        import app.database.postgres_connection as app_pc
        import src.app.database.postgres_connection as src_pc
        from app.database import is_moderation_enabled, upsert_awaiting_rights_group
        from src.app.handlers.status_handlers import _handle_bot_added

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
                await _handle_bot_added(
                    event, DISCUSSION_ID, HUMAN_ID, "Discussion", "member"
                )

            # No admin rights → activation must NOT have happened
            assert await is_moderation_enabled(DISCUSSION_ID) is False
        finally:
            src_pc._pool = None


class TestNoRightsFlagClearOnAdminAdd:
    """Fix #1: promotion (member→admin) clears no_rights_detected_at."""

    @pytest.mark.asyncio
    async def test_admin_add_clears_no_rights_flag(self):
        """Bot promoted to admin → clear_no_rights_detected_at called."""
        event = _real_event(
            from_id=HUMAN_ID,
            old_status="member",
            new_status="administrator",
            chat_id=DISCUSSION_ID,
            can_delete=True,
            can_restrict=True,
        )
        mock_clear = AsyncMock()

        with (
            patch(
                "src.app.handlers.status_handlers.update_group_admins",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers.activate_discussion_group",
                AsyncMock(return_value=False),
            ),
            patch(
                "src.app.handlers.status_handlers.clear_no_rights_detected_at",
                mock_clear,
            ),
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
                "src.app.handlers.status_handlers.bot.send_message",
                AsyncMock(),
            ),
        ):
            from src.app.handlers.status_handlers import _handle_bot_added

            await _handle_bot_added(
                event, DISCUSSION_ID, HUMAN_ID, "Discussion", "administrator"
            )

        mock_clear.assert_awaited_once_with(DISCUSSION_ID)

    @pytest.mark.asyncio
    async def test_member_add_does_not_clear_flag_and_notifies_once(self):
        """Bot added as plain member → flag NOT cleared; admins notified once."""
        event = _real_event(
            from_id=HUMAN_ID,
            old_status="left",
            new_status="member",
            chat_id=DISCUSSION_ID,
        )
        mock_clear = AsyncMock()
        mock_notify = AsyncMock()

        with (
            patch(
                "src.app.handlers.status_handlers.update_group_admins",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers.clear_no_rights_detected_at",
                mock_clear,
            ),
            patch(
                "src.app.handlers.status_handlers.set_no_rights_detected_at",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers._notify_admins_about_rights",
                mock_notify,
            ),
        ):
            from src.app.handlers.status_handlers import _handle_bot_added

            await _handle_bot_added(
                event, DISCUSSION_ID, HUMAN_ID, "Discussion", "member"
            )

        mock_clear.assert_not_awaited()
        mock_notify.assert_awaited_once()


class TestServiceMessageDeleteNoAdminSkip:
    """Fix #2: skip the no-rights nag when the bot isn't an admin in the chat."""

    def _permission_error(self) -> TelegramBadRequest:
        return TelegramBadRequest(
            method="deleteMessage", message="message can't be deleted"
        )

    def _message(self, *, chat_id: int = DISCUSSION_ID, join: bool = True) -> MagicMock:
        msg = MagicMock()
        msg.chat = MagicMock()
        msg.chat.id = chat_id
        msg.chat.title = "Discussion"
        msg.chat.username = None
        msg.message_id = 999
        if join:
            msg.new_chat_member = MagicMock()
            msg.new_chat_members = None
            msg.left_chat_member = None
        else:
            msg.new_chat_member = None
            msg.new_chat_members = None
            msg.left_chat_member = MagicMock()
        return msg

    def _admin_member(self) -> ChatMemberAdministrator:
        return ChatMemberAdministrator(
            user=User(id=BOT_ID, is_bot=True, first_name="Bot"),
            status="administrator",
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=False,
            can_restrict_members=True,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=True,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
        )

    def _base_patches(self, *, member) -> list:
        return [
            patch(
                "src.app.handlers.status_handlers.bot.delete_message",
                AsyncMock(side_effect=self._permission_error()),
            ),
            patch(
                "src.app.handlers.status_handlers.bot.get_chat_member",
                AsyncMock(return_value=member),
            ),
            patch(
                "src.app.handlers.status_handlers.set_no_rights_detected_at",
                AsyncMock(),
            ),
            patch(
                "src.app.handlers.status_handlers.bot.get_chat_administrators",
                AsyncMock(
                    return_value=[
                        MagicMock(
                            user=User(id=HUMAN_ID, is_bot=False, first_name="Admin")
                        )
                    ]
                ),
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
                "src.app.handlers.status_handlers.notify_admins_with_fallback_and_cleanup",
                AsyncMock(
                    return_value={
                        "notified_private": [HUMAN_ID],
                        "group_notified": False,
                        "group_cleaned_up": False,
                    }
                ),
            ),
        ]

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_join_skip_when_not_admin(self):
        """Join service msg + bot is member → skip tag, no flag, no DM."""
        from src.app.handlers.status_handlers import handle_member_service_message

        member = ChatMemberMember(
            user=User(id=BOT_ID, is_bot=True, first_name="Bot"), status="member"
        )
        mock_set = AsyncMock()
        mock_notify = AsyncMock()
        with ExitStack() as stack:
            for p in self._base_patches(member=member):
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.set_no_rights_detected_at",
                    mock_set,
                )
            )
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.notify_admins_with_fallback_and_cleanup",
                    mock_notify,
                )
            )
            result = await handle_member_service_message(self._message())

        assert result == "service_message_delete_skipped_no_admin"
        mock_set.assert_not_awaited()
        mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_leave_skip_when_not_admin(self):
        """Leave service msg + bot is member → skip tag, no flag, no DM."""
        from src.app.handlers.status_handlers import handle_member_service_message

        member = ChatMemberMember(
            user=User(id=BOT_ID, is_bot=True, first_name="Bot"), status="member"
        )
        mock_set = AsyncMock()
        mock_notify = AsyncMock()
        with ExitStack() as stack:
            for p in self._base_patches(member=member):
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.set_no_rights_detected_at",
                    mock_set,
                )
            )
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.notify_admins_with_fallback_and_cleanup",
                    mock_notify,
                )
            )
            result = await handle_member_service_message(self._message(join=False))

        assert result == "service_message_delete_skipped_no_admin"
        mock_set.assert_not_awaited()
        mock_notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nag_preserved_when_bot_is_admin(self):
        """Bot IS admin but lacks delete rights → nag + flag preserved."""
        from src.app.handlers.status_handlers import handle_member_service_message

        mock_set = AsyncMock()
        with ExitStack() as stack:
            for p in self._base_patches(member=self._admin_member()):
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.set_no_rights_detected_at",
                    mock_set,
                )
            )
            result = await handle_member_service_message(self._message())

        assert result == "service_message_no_rights"
        mock_set.assert_awaited_once_with(DISCUSSION_ID)

    @pytest.mark.asyncio
    async def test_membership_check_fails_falls_back_loud(self):
        """get_chat_member raises → fall back to current loud behavior."""
        from src.app.handlers.status_handlers import handle_member_service_message

        mock_set = AsyncMock()
        mock_notify = AsyncMock(
            return_value={
                "notified_private": [HUMAN_ID],
                "group_notified": False,
                "group_cleaned_up": False,
            }
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.bot.delete_message",
                    AsyncMock(side_effect=self._permission_error()),
                )
            )
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.bot.get_chat_member",
                    AsyncMock(
                        side_effect=TelegramBadRequest(
                            method="getChatMember", message="chat not found"
                        )
                    ),
                )
            )
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.set_no_rights_detected_at",
                    mock_set,
                )
            )
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.bot.get_chat_administrators",
                    AsyncMock(
                        return_value=[
                            MagicMock(
                                user=User(id=HUMAN_ID, is_bot=False, first_name="Admin")
                            )
                        ]
                    ),
                )
            )
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers._resolve_lang",
                    AsyncMock(return_value="en"),
                )
            )
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.format_chat_or_channel_display",
                    return_value="Discussion",
                )
            )
            stack.enter_context(
                patch(
                    "src.app.handlers.status_handlers.notify_admins_with_fallback_and_cleanup",
                    mock_notify,
                )
            )
            result = await handle_member_service_message(self._message())

        assert result == "service_message_no_rights"
        mock_set.assert_awaited_once_with(DISCUSSION_ID)
        mock_notify.assert_awaited_once()
