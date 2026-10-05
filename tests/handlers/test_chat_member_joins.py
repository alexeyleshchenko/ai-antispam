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
