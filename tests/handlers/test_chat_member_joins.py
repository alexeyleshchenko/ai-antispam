"""Tests for the `chat_member` join observer (issue #108, close leg 1).

Two observers write the join store and this is the reliable one: the service
message can be suppressed by the group's settings and is deleted by this bot
moments after it appears, while `chat_member` fires for every status change.
What must be pinned here is the DISCRIMINATION — a transition into the group
is a join, a promotion or a mute inside it is not — because over-recording is
harmless (it only makes the trust predicate more conservative) while a missed
join is exactly the blind spot this feature exists to close.
"""

from unittest.mock import patch

import pytest
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberBanned,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberRestricted,
    ChatMemberUpdated,
    User,
)

from src.app.database.trust_operations import (
    JOIN_SOURCE_CHAT_MEMBER,
    has_join_record,
)

CHAT_ID = -1001800163907
MEMBER_ID = 880001

def _chat_member(status: str, user: User):
    """The aiogram model that actually carries `status` (each is a Literal)."""
    if status == "member":
        return ChatMemberMember(user=user, status="member")
    if status == "left":
        return ChatMemberLeft(user=user, status="left")
    if status == "kicked":
        return ChatMemberBanned(user=user, status="kicked", until_date=0)
    if status == "administrator":
        return ChatMemberAdministrator(
            user=user,
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
    if status == "restricted":
        return ChatMemberRestricted(
            user=user,
            status="restricted",
            is_member=True,
            until_date=0,
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_react_to_messages=True,
            can_edit_tag=True,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=False,
            can_manage_topics=False,
        )
    raise ValueError(f"unhandled status {status}")

def _event(
    *,
    old_status: str,
    new_status: str,
    user_id: int = MEMBER_ID,
    is_bot: bool = False,
) -> ChatMemberUpdated:
    chat = Chat(id=CHAT_ID, type="supergroup", title="Discussion")
    user = User(id=user_id, is_bot=is_bot, first_name="M")
    return ChatMemberUpdated(
        chat=chat,
        from_user=user,
        date=0,
        old_chat_member=_chat_member(old_status, user),
        new_chat_member=_chat_member(new_status, user),
    )

class TestChatMemberJoins:
    """The dual-module trap: DB fixtures patch `app.database.*`, the handler
    reads `src.app.database.*`. Sync the pool so the real path runs."""

    def _sync_pool(self):
        import app.database.postgres_connection as app_pc
        import src.app.database.postgres_connection as src_pc

        src_pc._pool = app_pc._pool

    def _clear_pool(self):
        import src.app.database.postgres_connection as src_pc

        src_pc._pool = None

    @pytest.mark.asyncio
    async def test_left_to_member_is_recorded(self, patched_db_conn, clean_db):
        from src.app.handlers.status_handlers import handle_chat_member_update

        self._sync_pool()
        try:
            result = await handle_chat_member_update(
                _event(old_status="left", new_status="member")
            )
            recorded = await has_join_record(CHAT_ID, MEMBER_ID)
        finally:
            self._clear_pool()

        assert result == "chat_member_handled"
        assert recorded is True

    @pytest.mark.asyncio
    async def test_join_row_carries_the_chat_member_source(
        self, patched_db_conn, clean_db
    ):
        from src.app.handlers.status_handlers import handle_chat_member_update

        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="left", new_status="member")
            )
            async with clean_db.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT source FROM member_joins "
                    "WHERE group_id = $1 AND member_id = $2",
                    CHAT_ID,
                    MEMBER_ID,
                )
        finally:
            self._clear_pool()

        assert row is not None
        assert row["source"] == JOIN_SOURCE_CHAT_MEMBER

    @pytest.mark.asyncio
    async def test_kicked_to_member_is_recorded(self, patched_db_conn, clean_db):
        """A rejoin after a kick is still a join."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="kicked", new_status="member")
            )
            recorded = await has_join_record(CHAT_ID, MEMBER_ID)
        finally:
            self._clear_pool()

        assert recorded is True

    @pytest.mark.asyncio
    async def test_member_to_member_is_not_recorded(self, patched_db_conn, clean_db):
        """A status change INSIDE the group is not a join."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="member", new_status="member")
            )
            recorded = await has_join_record(CHAT_ID, MEMBER_ID)
        finally:
            self._clear_pool()

        assert recorded is False

    @pytest.mark.asyncio
    async def test_member_to_left_is_not_recorded(self, patched_db_conn, clean_db):
        """A leave is the opposite of a join."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="member", new_status="left")
            )
            recorded = await has_join_record(CHAT_ID, MEMBER_ID)
        finally:
            self._clear_pool()

        assert recorded is False

    @pytest.mark.asyncio
    async def test_bot_join_is_not_recorded(self, patched_db_conn, clean_db):
        """Bots are not moderated, so they are not trust candidates."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="left", new_status="member", is_bot=True)
            )
            recorded = await has_join_record(CHAT_ID, MEMBER_ID)
        finally:
            self._clear_pool()

        assert recorded is False

    @pytest.mark.asyncio
    async def test_a_store_failure_does_not_raise(self):
        """Never raises: a miss only makes the predicate more conservative."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        with patch(
            "src.app.handlers.status_handlers.record_join",
            side_effect=RuntimeError("store unavailable"),
        ):
            result = await handle_chat_member_update(
                _event(old_status="left", new_status="member")
            )

        assert result == "chat_member_handled"


# --- #130: the roster writers that ride this same update ---------------------
#
# This handler's join predicate requires `old in (left, kicked)`, so a
# `member -> administrator` promotion fell through and was discarded. Nothing
# else wrote the roster either: `get_chat_administrators` was called at seven
# sites and persisted at none, so 45 of 84 admin seats across 13 groups were
# invisible to `validation` — which classified one of them at 0.90, deleted his
# message, failed to ban him and charged the group's owner 1★ for the review.


class TestChatMemberRosterWrites:
    def _sync_pool(self):
        import app.database.postgres_connection as app_pc
        import src.app.database.postgres_connection as src_pc

        src_pc._pool = app_pc._pool

    def _clear_pool(self):
        import src.app.database.postgres_connection as src_pc

        src_pc._pool = None

    async def _seat(self, clean_db, admin_id: int = MEMBER_ID):
        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT is_registered, is_admin_now FROM group_administrators "
                "WHERE group_id = $1 AND admin_id = $2",
                CHAT_ID,
                admin_id,
            )
            credits = await conn.fetchval(
                "SELECT credits FROM administrators WHERE admin_id = $1", admin_id
            )
        return row, credits

    async def _seed_group(self, clean_db) -> None:
        """`group_administrators` has FKs to both parents — SQLite enforces them."""
        async with clean_db.acquire() as conn:
            await conn.execute(
                "INSERT INTO groups (group_id, title) VALUES ($1, 'Discussion') "
                "ON CONFLICT DO NOTHING",
                CHAT_ID,
            )

    async def _seed_seat(self, clean_db, *, registered: bool) -> None:
        await self._seed_group(clean_db)
        async with clean_db.acquire() as conn:
            await conn.execute(
                "INSERT INTO administrators (admin_id, credits) VALUES ($1, 0) "
                "ON CONFLICT DO NOTHING",
                MEMBER_ID,
            )
            await conn.execute(
                "INSERT INTO group_administrators "
                "(group_id, admin_id, is_registered, is_admin_now) "
                "VALUES ($1, $2, $3, TRUE) ON CONFLICT DO NOTHING",
                CHAT_ID,
                MEMBER_ID,
                registered,
            )

    @pytest.mark.asyncio
    async def test_promotion_writes_a_roster_seat(self, patched_db_conn, clean_db):
        """`member -> administrator` is a ROSTER change and must be recorded."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        await self._seed_group(clean_db)
        self._sync_pool()
        try:
            result = await handle_chat_member_update(
                _event(old_status="member", new_status="administrator")
            )
            row, credits = await self._seat(clean_db)
        finally:
            self._clear_pool()

        assert result == "chat_member_handled"
        assert row is not None, "a promotion must reach the roster"
        assert bool(row["is_admin_now"]) is True
        assert bool(row["is_registered"]) is False, (
            "a promotion must never make someone a billing contact"
        )
        assert credits == 0, "a promotion must never hand out the initial credits"

    @pytest.mark.asyncio
    async def test_promotion_from_left_is_a_join_and_a_seat(
        self, patched_db_conn, clean_db
    ):
        """One update, two writes: the trust store and the roster."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        await self._seed_group(clean_db)
        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="left", new_status="administrator")
            )
            joined = await has_join_record(CHAT_ID, MEMBER_ID)
            row, _ = await self._seat(clean_db)
        finally:
            self._clear_pool()

        assert joined is True
        assert row is not None and bool(row["is_admin_now"]) is True

    @pytest.mark.asyncio
    async def test_demotion_flips_the_flag_and_keeps_the_row(
        self, patched_db_conn, clean_db
    ):
        """A demoted PAYING admin keeps billing, stops being skipped."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        await self._seed_seat(clean_db, registered=True)

        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="administrator", new_status="member")
            )
            row, _ = await self._seat(clean_db)
        finally:
            self._clear_pool()

        assert row is not None, "demotion must never delete the row"
        assert bool(row["is_admin_now"]) is False
        assert bool(row["is_registered"]) is True, (
            "deleting or downgrading the row would silently stop charging the payer"
        )

    @pytest.mark.asyncio
    async def test_demotion_of_an_unknown_user_creates_no_row(
        self, patched_db_conn, clean_db
    ):
        """UPDATE-only: a mute in a watched group must not grow the roster."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="administrator", new_status="member")
            )
            row, credits = await self._seat(clean_db)
        finally:
            self._clear_pool()

        assert row is None
        assert credits is None

    @pytest.mark.asyncio
    async def test_mute_is_not_a_roster_change(self, patched_db_conn, clean_db):
        """`member -> restricted` is a mute, not a promotion."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="member", new_status="restricted")
            )
            row, _ = await self._seat(clean_db)
        finally:
            self._clear_pool()

        assert row is None

    @pytest.mark.asyncio
    async def test_bot_promotion_writes_no_seat(self, patched_db_conn, clean_db):
        """A bot is neither moderated nor billed — and has no administrators row."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        self._sync_pool()
        try:
            await handle_chat_member_update(
                _event(old_status="member", new_status="administrator", is_bot=True)
            )
            row, credits = await self._seat(clean_db)
        finally:
            self._clear_pool()

        assert row is None
        assert credits is None

    @pytest.mark.asyncio
    async def test_a_roster_write_failure_does_not_raise(
        self, patched_db_conn, clean_db
    ):
        """The handler's Never-raises contract survives the new branch."""
        from src.app.handlers.status_handlers import handle_chat_member_update

        with patch(
            "src.app.handlers.status_handlers._apply_admin_status",
            side_effect=RuntimeError("store unavailable"),
        ):
            result = await handle_chat_member_update(
                _event(old_status="member", new_status="administrator")
            )

        assert result == "chat_member_handled"
