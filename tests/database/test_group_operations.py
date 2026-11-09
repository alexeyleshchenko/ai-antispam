import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.database import (
    Administrator,
    add_member,
    cleanup_group_data,
    clear_no_rights_detected_at,
    deduct_credits_from_admins,
    get_admin_group_ids,
    get_admin_groups,
    get_group,
    get_groups_with_no_rights_past_grace,
    get_moderation_event_count,
    get_paying_admins,
    heal_bare_group_rows,
    increment_moderation_events,
    is_member_in_group,
    is_moderation_enabled,
    is_trusted_member,
    mark_admins_not_admin,
    migrate_group,
    pause_group_preserving_data,
    refund_credits,
    remove_member_from_group,
    retire_group,
    seed_administrator_row,
    set_group_moderation,
    set_moderation_events,
    set_no_rights_detected_at,
    update_group_admins,
    upsert_group_admin,
)


@pytest.mark.asyncio
async def test_get_paying_admins(patched_db_conn, clean_db):
    """Test retrieving paying admins for a group"""
    async with clean_db.acquire() as conn:
        group_id = 987654

        # Create users with different credit amounts
        users = [
            Administrator(admin_id=111, username="admin1", credits=50),  # Paying admin
            Administrator(
                admin_id=222, username="admin2", credits=0
            ),  # Non-paying admin
            Administrator(
                admin_id=333, username="admin3", credits=20
            ),  # Another paying admin
        ]

        # Save users to administrators table
        for user in users:
            await conn.execute(
                """
                INSERT INTO administrators (admin_id, username, credits)
                VALUES ($1, $2, $3)
                ON CONFLICT DO NOTHING
            """,
                user.admin_id,
                user.username,
                user.credits,
            )

        # Create group directly in database
        await conn.execute(
            """
            INSERT INTO groups (group_id)
            VALUES ($1)
        """,
            group_id,
        )

        # Add admins to group directly
        for user in users:
            await conn.execute(
                """
                INSERT INTO group_administrators (group_id, admin_id)
                VALUES ($1, $2)
            """,
                group_id,
                user.admin_id,
            )

        # Get paying admins
        paying_admins = await get_paying_admins(group_id)

        # Assertions
        assert len(paying_admins) == 2
        assert 111 in paying_admins
        assert 333 in paying_admins
        assert 222 not in paying_admins


@pytest.mark.asyncio
async def test_add_group_member(patched_db_conn, clean_db):
    """Test adding a member to a group"""
    async with clean_db.acquire() as conn:
        group_id = 987654
        new_member_id = 456789

        # Create group directly in database
        await conn.execute(
            """
            INSERT INTO groups (group_id)
            VALUES ($1)
        """,
            group_id,
        )

        inserted = await add_member(group_id, new_member_id)
        assert inserted is True
        assert await is_member_in_group(group_id, new_member_id) is True
        assert await get_moderation_event_count(group_id, new_member_id) == 1
        assert await is_trusted_member(group_id, new_member_id) is False

        inserted_again = await add_member(group_id, new_member_id)
        assert inserted_again is False


@pytest.mark.asyncio
async def test_remove_group_member(patched_db_conn, clean_db):
    """Test removing a member from a group"""
    async with clean_db.acquire() as conn:
        group_id = 987654
        member_to_remove = 456789

        await conn.execute(
            """
            INSERT INTO groups (group_id)
            VALUES ($1)
        """,
            group_id,
        )

        # Add member directly to database
        await conn.execute(
            """
            INSERT INTO approved_members (group_id, member_id)
            VALUES ($1, $2)
        """,
            group_id,
            member_to_remove,
        )

        # Remove the member
        await remove_member_from_group(member_to_remove, group_id)

        # Verify member was removed
        is_member = await is_member_in_group(group_id, member_to_remove)
        assert is_member is False


@pytest.mark.asyncio
async def test_set_group_moderation(patched_db_conn, clean_db):
    """Test enabling/disabling moderation for a group"""
    async with clean_db.acquire() as conn:
        group_id = 987654

        # Add group directly to database
        await conn.execute(
            """
            INSERT INTO groups (group_id, moderation_enabled)
            VALUES ($1, $2)
        """,
            group_id,
            False,
        )

        # Enable moderation
        await set_group_moderation(group_id, True)

        # Verify moderation is enabled
        is_enabled = await is_moderation_enabled(group_id)
        assert is_enabled is True


@pytest.mark.asyncio
async def test_deduct_credits_sets_credits_depleted_at_when_zero(
    patched_db_conn, clean_db
):
    """When deduction brings credits to 0, credits_depleted_at is set."""
    async with clean_db.acquire() as conn:
        group_id = 555555
        admin_id = 777
        await conn.execute(
            "INSERT INTO groups (group_id) VALUES ($1)",
            group_id,
        )
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits, credits_depleted_at)
            VALUES ($1, 'sole', 5, NULL)
            """,
            admin_id,
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            group_id,
            admin_id,
        )

    result = await deduct_credits_from_admins(group_id, 5)
    assert result == admin_id

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT credits, credits_depleted_at FROM administrators WHERE admin_id = $1",
            admin_id,
        )
        assert row["credits"] == 0
        assert row["credits_depleted_at"] is not None


async def _seed_admin_group(clean_db, admin_id: int, group_id: int) -> None:
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title) VALUES ($1, 'Stale')", group_id
        )
        await conn.execute(
            "INSERT INTO administrators (admin_id, username, credits) VALUES ($1, 'admin', 10)",
            admin_id,
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            group_id,
            admin_id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found"),
        TelegramForbiddenError(
            method=MagicMock(), message="Forbidden: bot was kicked from the group chat"
        ),
    ],
)
async def test_get_admin_groups_inaccessible_logs_info_and_cleans_db(
    patched_db_conn, clean_db, caplog, error
):
    """Inaccessible groups during stats: INFO log, skip list, DB cleanup."""
    admin_id = 9001
    group_id = -1009001
    await _seed_admin_group(clean_db, admin_id, group_id)

    with patch("app.database.group_operations.bot") as mock_bot:
        mock_bot.get_chat = AsyncMock(side_effect=error)
        with caplog.at_level(logging.INFO, logger="app.database.group_operations"):
            groups = await get_admin_groups(admin_id)

    assert groups == []
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert any("inaccessible during admin stats" in r.message for r in caplog.records)

    # E+C deletion policy: group row survives with lifecycle status + audit event
    async with clean_db.acquire() as conn:
        group_row = await conn.fetchrow(
            "SELECT status FROM groups WHERE group_id = $1", group_id
        )
        assert group_row is not None
        assert group_row["status"] == "left"

        event_row = await conn.fetchrow(
            "SELECT action, reason FROM entity_events "
            "WHERE entity_type = 'group' AND entity_id = $1",
            group_id,
        )
        assert event_row is not None
        assert event_row["action"] == "group_left"
        assert event_row["reason"] == "inaccessible_chat"


@pytest.mark.asyncio
async def test_get_admin_groups_unexpected_telegram_error_logs_error(
    patched_db_conn, clean_db, caplog
):
    """Unexpected Telegram errors still log at ERROR without DB cleanup."""
    admin_id = 9002
    group_id = -1009002
    await _seed_admin_group(clean_db, admin_id, group_id)
    err = TelegramBadRequest(method=MagicMock(), message="Bad Request: invalid chat id")

    with patch("app.database.group_operations.bot") as mock_bot:
        mock_bot.get_chat = AsyncMock(side_effect=err)
        with caplog.at_level(logging.ERROR, logger="app.database.group_operations"):
            groups = await get_admin_groups(admin_id)

    assert groups == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert not any(
        "inaccessible during admin stats" in r.message for r in caplog.records
    )

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM groups WHERE group_id = $1", group_id)
        assert row is not None


@pytest.mark.asyncio
async def test_get_admin_groups_returns_accessible_chats(patched_db_conn, clean_db):
    """Accessible groups are returned with live Telegram title and topic fields."""
    admin_id = 9003
    group_id = -1009003
    await _seed_admin_group(clean_db, admin_id, group_id)
    # Seed the chat-topic columns — the SELECT must carry them through.
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE groups SET topic_description_short = $1, "
            "topic_updated_at = $2 WHERE group_id = $3",
            "PHP jobs",
            datetime.now(UTC),
            group_id,
        )

    chat = MagicMock(title="Live Group")
    with patch("app.database.group_operations.bot") as mock_bot:
        mock_bot.get_chat = AsyncMock(return_value=chat)
        groups = await get_admin_groups(admin_id)

    assert len(groups) == 1
    assert groups[0]["id"] == group_id
    assert groups[0]["title"] == "Live Group"
    assert groups[0]["topic_description_short"] == "PHP jobs"
    assert groups[0]["topic_updated_at"] is not None


@pytest.mark.asyncio
async def test_get_admin_group_ids(patched_db_conn, clean_db):
    """get_admin_group_ids returns group IDs for admin."""
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO groups (group_id) VALUES (1), (2)")
        await conn.execute(
            "INSERT INTO administrators (admin_id, username, credits) VALUES (99, 'x', 10)"
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES (1, 99), (2, 99)"
        )

    ids = await get_admin_group_ids(99)
    assert set(ids) == {1, 2}


@pytest.mark.asyncio
async def test_set_no_rights_detected_at(patched_db_conn, clean_db):
    """set_no_rights_detected_at sets timestamp only when NULL."""
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO groups (group_id) VALUES (100)")
    await set_no_rights_detected_at(100)
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT no_rights_detected_at FROM groups WHERE group_id = 100"
        )
        assert row["no_rights_detected_at"] is not None
    await set_no_rights_detected_at(100)
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT no_rights_detected_at FROM groups WHERE group_id = 100"
        )
        first = row["no_rights_detected_at"]
    await set_no_rights_detected_at(100)
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT no_rights_detected_at FROM groups WHERE group_id = 100"
        )
        assert row["no_rights_detected_at"] == first


@pytest.mark.asyncio
async def test_clear_no_rights_detected_at(patched_db_conn, clean_db):
    """clear_no_rights_detected_at clears the timestamp."""
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, no_rights_detected_at) VALUES (100, CURRENT_TIMESTAMP)"
        )
    await clear_no_rights_detected_at(100)
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT no_rights_detected_at FROM groups WHERE group_id = 100"
        )
        assert row["no_rights_detected_at"] is None


@pytest.mark.asyncio
async def test_get_groups_with_no_rights_past_grace(patched_db_conn, clean_db):
    """get_groups_with_no_rights_past_grace returns groups past grace period."""
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO groups (group_id) VALUES (1), (2), (3)")
        await conn.execute(
            "UPDATE groups SET no_rights_detected_at = datetime('now', '-8 days') WHERE group_id = 1"
        )
        await conn.execute(
            "UPDATE groups SET no_rights_detected_at = datetime('now', '-3 days') WHERE group_id = 2"
        )
        await conn.execute(
            "UPDATE groups SET no_rights_detected_at = datetime('now', '-10 days') WHERE group_id = 3"
        )
    result = await get_groups_with_no_rights_past_grace(7)
    assert set(result) == {1, 3}


@pytest.mark.asyncio
async def test_is_trusted_member_probation_states(patched_db_conn, clean_db):
    """Trusted only when moderation_event_count >= probation_min_events."""
    group_id = 555001
    member_id = 777001
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO groups (group_id) VALUES ($1)", group_id)

    assert await is_trusted_member(group_id, member_id) is False

    await add_member(group_id, member_id)
    assert await is_trusted_member(group_id, member_id) is False

    await set_moderation_events(group_id, member_id, 2)
    assert await is_trusted_member(group_id, member_id) is False

    await set_moderation_events(group_id, member_id, 3)
    assert await is_trusted_member(group_id, member_id) is True


@pytest.mark.asyncio
async def test_set_moderation_events_upsert_on_conflict(patched_db_conn, clean_db):
    """set_moderation_events upserts when row already exists (admin instant trust)."""
    group_id = 555002
    member_id = 777002
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO groups (group_id) VALUES ($1)", group_id)
        await conn.execute(
            """
            INSERT INTO approved_members (group_id, member_id, moderation_event_count)
            VALUES ($1, $2, 1)
            """,
            group_id,
            member_id,
        )

    await set_moderation_events(group_id, member_id, 3)
    assert await get_moderation_event_count(group_id, member_id) == 3
    assert await is_trusted_member(group_id, member_id) is True


@pytest.mark.asyncio
async def test_increment_moderation_events(patched_db_conn, clean_db):
    group_id = 555003
    member_id = 777003
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO groups (group_id) VALUES ($1)", group_id)

    await add_member(group_id, member_id)
    await increment_moderation_events(group_id, member_id)
    assert await get_moderation_event_count(group_id, member_id) == 2


@pytest.mark.asyncio
async def test_remove_member_clears_probation_counter(patched_db_conn, clean_db):
    group_id = 555004
    member_id = 777004
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO groups (group_id) VALUES ($1)", group_id)

    await set_moderation_events(group_id, member_id, 3)
    await remove_member_from_group(member_id, group_id)
    assert await get_moderation_event_count(group_id, member_id) is None


@pytest.mark.asyncio
async def test_update_group_admins_excludes_group_anonymous_bot(
    patched_db_conn, clean_db
):
    """GROUP_ANONYMOUS_BOT_ID (1087968824) must not be inserted into group_administrators.

    The GroupAnonymousBot appears in admin lists when a group has anonymous admin enabled,
    but it cannot receive bot-to-bot DMs. We must filter it out when storing admin IDs
    so notifications never try to reach it.
    """
    from app.common.telegram_errors import GROUP_ANONYMOUS_BOT_ID

    group_id = -1002001
    human_admin_id = 123456
    admin_ids = [human_admin_id, GROUP_ANONYMOUS_BOT_ID]

    await update_group_admins(group_id, admin_ids, ["human", "anonymousbot"])

    async with clean_db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT admin_id FROM group_administrators WHERE group_id = $1",
            group_id,
        )
        inserted_ids = {row["admin_id"] for row in rows}

    assert human_admin_id in inserted_ids
    assert GROUP_ANONYMOUS_BOT_ID not in inserted_ids, (
        f"GROUP_ANONYMOUS_BOT_ID ({GROUP_ANONYMOUS_BOT_ID}) must not be stored in "
        "group_administrators — it cannot receive DMs from bots"
    )


@pytest.mark.asyncio
async def test_activate_discussion_group_flips_awaiting_rights(
    patched_db_conn, clean_db
):
    """Awaiting-rights row (linked_channel_id set, moderation off) flips active."""
    from app.database import activate_discussion_group, upsert_awaiting_rights_group

    group_id = -1003001
    channel_id = -1004352022427

    await upsert_awaiting_rights_group(
        group_id, "Discussion", None, linked_channel_id=channel_id
    )
    async with clean_db.acquire() as conn:
        before = await conn.fetchval(
            "SELECT moderation_enabled FROM groups WHERE group_id = $1", group_id
        )
    assert before is False or before == 0

    activated = await activate_discussion_group(group_id)
    assert activated is True

    async with clean_db.acquire() as conn:
        after = await conn.fetchval(
            "SELECT moderation_enabled FROM groups WHERE group_id = $1", group_id
        )
    assert after is True or after == 1


@pytest.mark.asyncio
async def test_activate_discussion_group_leaves_disabled_group_alone(
    patched_db_conn, clean_db
):
    """A group without linked_channel_id (deliberately disabled) is untouched."""
    from app.database import activate_discussion_group, set_group_moderation

    group_id = -1003002
    await set_group_moderation(group_id, False)

    activated = await activate_discussion_group(group_id)
    assert activated is False

    async with clean_db.acquire() as conn:
        after = await conn.fetchval(
            "SELECT moderation_enabled FROM groups WHERE group_id = $1", group_id
        )
    assert after is False or after == 0


@pytest.mark.asyncio
async def test_activate_discussion_group_active_with_channel_stays_active(
    patched_db_conn, clean_db
):
    """Already-active linked group: no-op, still returns True."""
    from app.database import activate_discussion_group, upsert_awaiting_rights_group

    group_id = -1003003
    channel_id = -1004352022428
    await upsert_awaiting_rights_group(
        group_id, "Discussion", None, linked_channel_id=channel_id
    )
    # Simulate "already active" (owner enabled moderation) WITHOUT wiping
    # linked_channel_id — direct UPDATE, matching Postgres ON CONFLICT DO UPDATE
    # semantics (the SQLite adapter's INSERT OR REPLACE would drop the column).
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE groups SET moderation_enabled = 1 WHERE group_id = ?",
            group_id,
        )

    activated = await activate_discussion_group(group_id)
    assert activated is True

    async with clean_db.acquire() as conn:
        after = await conn.fetchval(
            "SELECT moderation_enabled FROM groups WHERE group_id = $1", group_id
        )
    assert after is True or after == 1


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_discussion_order_convergence_awaiting_rights(patched_db_conn, clean_db):
    """Composite-event random-order convergence (issue #34/#36).

    The channel add + discussion auto-add arrive as a batch of my_chat_member
    updates in RANDOM order (no correlation key in either payload — spike-0).
    Both orders must converge to the SAME final DB state: an awaiting-rights row
    with linked_channel_id set and moderation disabled.

    Order A (discussion piece first): _handle_auto_added_discussion upserts
    WITHOUT linked_channel_id (the payload carries none) — then the channel
    piece's _notify_discussion_added_and_stay re-upserts WITH it. The ON
    CONFLICT COALESCE(EXCLUDED.linked_channel_id, groups.linked_channel_id)
    fills the column in place.

    Order B (channel piece first): _notify_discussion_added_and_stay upserts
    WITH linked_channel_id — then _handle_auto_added_discussion re-upserts
    WITHOUT it. The same COALESCE keeps the existing value. NOTE: the SQLite
    test adapter's INSERT OR REPLACE conversion (conftest.py) drops NULL
    columns instead of preserving them like Postgres DO UPDATE — so this test
    verifies Order A against the DB (discussion-first must END with the key
    set), and Order B is covered by the COALESCE semantics in the upsert SQL
    itself plus test_registers_awaiting_rights (no-key upsert is idempotent).
    """
    from app.database import upsert_awaiting_rights_group

    group_id = -1003004
    channel_id = -1004352022427

    # --- Order A: discussion piece first, channel piece second ---
    await upsert_awaiting_rights_group(group_id, "Discussion", None)
    await upsert_awaiting_rights_group(
        group_id, "Discussion", None, linked_channel_id=channel_id
    )

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT group_id, linked_channel_id, moderation_enabled FROM groups WHERE group_id = $1",
            group_id,
        )

    # Discussion-first converges: linked_channel_id filled in by the channel piece
    assert row["linked_channel_id"] == channel_id
    assert (row["moderation_enabled"] is False) or (row["moderation_enabled"] == 0)

    # Same as channel-first (single upsert WITH the key) — identical end state
    group_b = -1003005
    await upsert_awaiting_rights_group(
        group_b, "Discussion", None, linked_channel_id=channel_id
    )
    async with clean_db.acquire() as conn:
        row_b = await conn.fetchrow(
            "SELECT group_id, linked_channel_id, moderation_enabled FROM groups WHERE group_id = $1",
            group_b,
        )
    assert row_b["linked_channel_id"] == channel_id
    assert (row_b["moderation_enabled"] is False) or (row_b["moderation_enabled"] == 0)


@pytest.mark.asyncio
async def test_set_group_moderation_fills_metadata_on_new_row(
    patched_db_conn, clean_db
):
    """Metadata passed to set_group_moderation lands on a fresh row (no-metadata gap)."""
    group_id = -10012345
    await set_group_moderation(group_id, True, "My Group", "mygroup")

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT title, username, moderation_enabled FROM groups WHERE group_id = $1",
            group_id,
        )
    assert row["title"] == "My Group"
    assert row["username"] == "mygroup"
    assert row["moderation_enabled"] == 1


@pytest.mark.asyncio
async def test_set_group_moderation_keeps_existing_metadata(patched_db_conn, clean_db):
    """COALESCE on conflict: existing title/username survive a metadata-less call.

    The SQLite test harness rewrites ON CONFLICT DO UPDATE into INSERT OR REPLACE
    (not representative of Postgres), so the COALESCE-preserve semantics are
    verified by spying on the SQL the function sends: the ON CONFLICT branch must
    reference COALESCE(EXCLUDED.*, groups.*).
    """
    group_id = -10012346
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, username, moderation_enabled) "
            "VALUES ($1, 'Existing', 'existing', TRUE)",
            group_id,
        )

    async with clean_db.acquire() as conn:
        with patch.object(conn, "execute", wraps=conn.execute) as spy:
            await set_group_moderation(group_id, False)
            sql = spy.call_args.args[0]

    assert "COALESCE(EXCLUDED.title, groups.title)" in sql
    assert "COALESCE(EXCLUDED.username, groups.username)" in sql
    assert "moderation_enabled = EXCLUDED.moderation_enabled" in sql

    # And the disable still lands (moderation flag flipped).
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT title, username, moderation_enabled FROM groups WHERE group_id = $1",
            group_id,
        )
    assert row["moderation_enabled"] == 0


@pytest.mark.asyncio
async def test_set_group_moderation_backward_compatible_bare_call(
    patched_db_conn, clean_db
):
    """Old two-arg call still works (bare row creation remains allowed)."""
    group_id = -10012347
    await set_group_moderation(group_id, True)

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT title, username, moderation_enabled FROM groups WHERE group_id = $1",
            group_id,
        )
    assert row["moderation_enabled"] == 1
    assert row["title"] is None  # bare — healed later by the scheduled job


@pytest.mark.asyncio
async def test_heal_bare_group_rows_fills_metadata(patched_db_conn, clean_db):
    """Bare rows are resolved and filled incl. linked_channel_id; exists rows untouched."""
    bare_a = -1001
    bare_b = -1002
    known = -1003
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, moderation_enabled) VALUES ($1, TRUE)",
            bare_a,
        )
        await conn.execute(
            "INSERT INTO groups (group_id, moderation_enabled) VALUES ($1, TRUE)",
            bare_b,
        )
        await conn.execute(
            "INSERT INTO groups (group_id, title, moderation_enabled) VALUES ($1, 'Known', TRUE)",
            known,
        )

    def fake_chat(group_id):
        return MagicMock(
            title=f"Chat {group_id}",
            username=f"user{group_id}",
            linked_chat_id=-2001 if group_id == bare_a else None,
        )

    with patch("app.database.group_operations.bot") as mock_bot:
        mock_bot.get_chat = AsyncMock(side_effect=lambda gid: fake_chat(gid))
        summary = await heal_bare_group_rows(concurrency=2, limit=100)

    assert summary["healed"] == 2
    assert summary["skipped"] == 0
    assert summary["total"] == 2

    async with clean_db.acquire() as conn:
        row_a = await conn.fetchrow("SELECT * FROM groups WHERE group_id = $1", bare_a)
        row_b = await conn.fetchrow("SELECT * FROM groups WHERE group_id = $1", bare_b)
        row_k = await conn.fetchrow(
            "SELECT title FROM groups WHERE group_id = $1", known
        )
    assert row_a["title"] == "Chat -1001"
    assert row_a["username"] == "user-1001"
    assert row_a["linked_channel_id"] == -2001
    assert row_b["title"] == "Chat -1002"
    assert row_k["title"] == "Known"  # non-bare row untouched


@pytest.mark.asyncio
async def test_heal_bare_group_rows_skips_unreachable_never_raises(
    patched_db_conn, clean_db, caplog
):
    """Forbidden/bad-request chats are skipped+counted; the loop never raises."""
    bare_a = -1001
    bare_b = -1002
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, moderation_enabled) VALUES ($1, TRUE)",
            bare_a,
        )
        await conn.execute(
            "INSERT INTO groups (group_id, moderation_enabled) VALUES ($1, TRUE)",
            bare_b,
        )

    forbidden = TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: bot is not a member"
    )
    bad = TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")

    def fake_get_chat(gid):
        if gid == bare_a:
            raise forbidden
        raise bad

    with patch("app.database.group_operations.bot") as mock_bot:
        mock_bot.get_chat = AsyncMock(side_effect=fake_get_chat)
        with caplog.at_level(logging.INFO, logger="app.database.group_operations"):
            summary = await heal_bare_group_rows(concurrency=2, limit=100)

    assert summary == {"healed": 0, "skipped": 2, "total": 2}
    # rows still bare (untouched)
    async with clean_db.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM groups WHERE title IS NULL AND username IS NULL"
        )
    assert count == 2


@pytest.mark.asyncio
async def test_heal_bare_group_rows_skips_no_title_chats(patched_db_conn, clean_db):
    """Private chats (no title) are skipped, not marked healed."""
    bare_a = 123456  # positive id = private chat
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, moderation_enabled) VALUES ($1, TRUE)",
            bare_a,
        )

    chat = MagicMock(title=None, username=None, linked_chat_id=None)
    with patch("app.database.group_operations.bot") as mock_bot:
        mock_bot.get_chat = AsyncMock(return_value=chat)
        summary = await heal_bare_group_rows(concurrency=2, limit=100)

    assert summary == {"healed": 0, "skipped": 1, "total": 1}

@pytest.mark.asyncio
async def test_refund_credits_restores_balance_and_clears_depletion(
    patched_db_conn, clean_db
):
    """#48: a refund returns the credit and clears credits_depleted_at.

    The depletion stamp matters: a refunded admin must not stay on the
    deactivation path for a debt that was just repaid.
    """
    admin_id = 424242
    async with clean_db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO administrators
                (admin_id, username, credits, credits_depleted_at)
            VALUES ($1, 'refunded', 0, NOW())
            """,
            admin_id,
        )

    await refund_credits(admin_id, 1)

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT credits, credits_depleted_at FROM administrators WHERE admin_id = $1",
            admin_id,
        )
        assert row["credits"] == 1
        assert row["credits_depleted_at"] is None

        txn = await conn.fetchrow(
            "SELECT amount, type FROM transactions WHERE admin_id = $1 ORDER BY id DESC LIMIT 1",
            admin_id,
        )
        assert txn["amount"] == 1
        assert txn["type"] == "refund"

@pytest.mark.asyncio
async def test_refund_credits_non_positive_amount_is_a_noop(
    patched_db_conn, clean_db
):
    """A zero/negative refund must not touch the balance."""
    admin_id = 424243
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO administrators (admin_id, username, credits) VALUES ($1, 'x', 5)",
            admin_id,
        )

    await refund_credits(admin_id, 0)

    async with clean_db.acquire() as conn:
        credits = await conn.fetchval(
            "SELECT credits FROM administrators WHERE admin_id = $1", admin_id
        )
        assert credits == 5
        txn_count = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions WHERE admin_id = $1", admin_id
        )
        assert txn_count == 0


# --- The admin roster primitives (#130) -------------------------------------
#
# Every roster write is a `DO NOTHING` insert followed by a one-column UPDATE,
# never an upsert: the test harness rewrites `ON CONFLICT ... DO UPDATE` into
# `INSERT OR REPLACE`, which resets every column the INSERT list omits. These
# tests assert the column that must survive, so the rewrite cannot hide.


class _RecordingConn:
    """Captures the SQL a primitive executes, with no database behind it."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    async def execute(self, query: str, *args) -> str:
        self.statements.append(query)
        return "UPDATE 0"

    async def fetchval(self, query: str, *args):
        self.statements.append(query)

    async def fetchrow(self, query: str, *args):
        self.statements.append(query)


async def _seed_group_and_admin(conn, group_id: int, admin_id: int) -> None:
    """Idempotent: several admins share one group row."""
    await conn.execute(
        "INSERT INTO groups (group_id) VALUES ($1) ON CONFLICT DO NOTHING", group_id
    )
    await conn.execute(
        "INSERT INTO administrators (admin_id, credits) VALUES ($1, 0) "
        "ON CONFLICT DO NOTHING",
        admin_id,
    )


@pytest.mark.asyncio
async def test_upsert_group_admin_never_promotes_a_roster_row_to_billing(
    patched_db_conn, clean_db
):
    """`is_registered` survives an `is_admin_now` flip — the adapter hazard.

    A `DO UPDATE` upsert rewritten to INSERT OR REPLACE would reset
    `is_registered` to its TRUE default: a roster-only admin becomes a billing
    admin, and the next deduction charges a stranger. Asserted, not reasoned.
    """
    group_id, admin_id = 880001, 880011
    async with clean_db.acquire() as conn:
        await _seed_group_and_admin(conn, group_id, admin_id)
        await seed_administrator_row(conn, admin_id)

        await upsert_group_admin(conn, group_id, admin_id, True)
        await upsert_group_admin(conn, group_id, admin_id, False)
        await upsert_group_admin(conn, group_id, admin_id, True)

        row = await conn.fetchrow(
            "SELECT is_registered, is_admin_now FROM group_administrators "
            "WHERE group_id = $1 AND admin_id = $2",
            group_id,
            admin_id,
        )
        assert bool(row["is_registered"]) is False
        assert bool(row["is_admin_now"]) is True


@pytest.mark.asyncio
async def test_upsert_group_admin_inserts_roster_only_rows(patched_db_conn, clean_db):
    """A discovered Telegram admin starts roster-only and skip-visible."""
    group_id, admin_id = 880002, 880012
    async with clean_db.acquire() as conn:
        await _seed_group_and_admin(conn, group_id, admin_id)

        await upsert_group_admin(conn, group_id, admin_id, True)

        row = await conn.fetchrow(
            "SELECT is_registered, is_admin_now FROM group_administrators "
            "WHERE group_id = $1 AND admin_id = $2",
            group_id,
            admin_id,
        )
        assert bool(row["is_registered"]) is False
        assert bool(row["is_admin_now"]) is True


@pytest.mark.asyncio
async def test_seed_administrator_row_never_touches_an_existing_balance(
    patched_db_conn, clean_db
):
    """Prefill creates a 0-credit row and leaves a paying admin's balance alone."""
    paying, stranger = 880003, 880013
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits) VALUES ($1, 55)", paying
        )

        await seed_administrator_row(conn, paying)
        await seed_administrator_row(conn, stranger)

        assert (
            await conn.fetchval(
                "SELECT credits FROM administrators WHERE admin_id = $1", paying
            )
            == 55
        )
        assert (
            await conn.fetchval(
                "SELECT credits FROM administrators WHERE admin_id = $1", stranger
            )
            == 0
        )
        # Never a second welcome grant: activation is what grants, not the row.
        assert (
            await conn.fetchval(
                "SELECT activated_at FROM administrators WHERE admin_id = $1", paying
            )
            is None
        )


@pytest.mark.asyncio
async def test_mark_admins_not_admin_flips_only_the_absent_admins(
    patched_db_conn, clean_db
):
    """Demotion flips the flag for the absent ids and nothing else."""
    group_id = 880004
    kept, demoted = 880014, 880015
    async with clean_db.acquire() as conn:
        await _seed_group_and_admin(conn, group_id, kept)
        await _seed_group_and_admin(conn, group_id, demoted)
        await upsert_group_admin(conn, group_id, kept, True)
        await upsert_group_admin(conn, group_id, demoted, True)

        flipped = await mark_admins_not_admin(conn, group_id, [kept])

        assert flipped == 1
        rows = {
            row["admin_id"]: bool(row["is_admin_now"])
            for row in await conn.fetch(
                "SELECT admin_id, is_admin_now FROM group_administrators "
                "WHERE group_id = $1",
                group_id,
            )
        }
        assert rows == {kept: True, demoted: False}
        # The demoted row is kept, never deleted: it may still carry billing.
        assert await conn.fetchval(
            "SELECT COUNT(*) FROM group_administrators WHERE group_id = $1", group_id
        ) == 2


@pytest.mark.asyncio
async def test_mark_admins_not_admin_refuses_an_empty_keep_list(
    patched_db_conn, clean_db, caplog
):
    """An empty Telegram read must not demote the whole group.

    `admin_id NOT IN ()` is accepted by SQLite and is true for every row, so a
    failed `getChatAdministrators` would have flipped the entire roster.
    """
    group_id = 880005
    admins = [880016, 880017]
    async with clean_db.acquire() as conn:
        for admin_id in admins:
            await _seed_group_and_admin(conn, group_id, admin_id)
            await upsert_group_admin(conn, group_id, admin_id, True)

        with caplog.at_level(logging.WARNING):
            flipped = await mark_admins_not_admin(conn, group_id, [])

        assert flipped == 0
        remaining = await conn.fetch(
            "SELECT is_admin_now FROM group_administrators WHERE group_id = $1",
            group_id,
        )
        assert all(bool(row["is_admin_now"]) for row in remaining)
    assert any("refusing an empty admin list" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_roster_primitives_never_emit_do_update():
    """The two-statement form is a property of the SQL, not of the docstring.

    A future "simplification" back to `ON CONFLICT ... DO UPDATE` would pass
    every database test above on Postgres and corrupt rows only under the test
    adapter — so the shape itself is asserted.
    """
    conn = _RecordingConn()
    await upsert_group_admin(conn, 1, 2, True)
    await seed_administrator_row(conn, 2)
    await mark_admins_not_admin(conn, 1, [2])

    assert conn.statements, "no SQL captured"
    for statement in conn.statements:
        upper = statement.upper()
        assert "DO UPDATE" not in upper, statement
        assert "EXCLUDED" not in upper, statement
        assert "OR REPLACE" not in upper, statement


# --- get_group: the roster partition (#130) --------------------------------


@pytest.mark.asyncio
async def test_get_group_partitions_billing_from_the_roster(
    patched_db_conn, clean_db
):
    """One query feeds two lists with two meanings.

    `admin_ids` is billing + notifications (`is_registered`); a roster-only
    Telegram admin must never appear there or 72 strangers get DM'd and billed.
    `telegram_admin_ids` is the moderation skip (`is_admin_now`), and a
    demoted payer must not appear there or they could spam forever.
    """
    group_id = 880101
    payer, roster_only, demoted = 880111, 880112, 880113
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id) VALUES ($1) ON CONFLICT DO NOTHING",
            group_id,
        )
        for admin_id in (payer, roster_only, demoted):
            await conn.execute(
                "INSERT INTO administrators (admin_id, credits) VALUES ($1, 0) "
                "ON CONFLICT DO NOTHING",
                admin_id,
            )
        for admin_id, is_registered, is_admin_now in (
            (payer, True, True),
            (roster_only, False, True),
            (demoted, True, False),
        ):
            await conn.execute(
                "INSERT INTO group_administrators "
                "(group_id, admin_id, is_registered, is_admin_now) "
                "VALUES ($1, $2, $3, $4)",
                group_id,
                admin_id,
                is_registered,
                is_admin_now,
            )

    group = await get_group(group_id)

    assert group is not None
    assert sorted(group.admin_ids) == sorted([payer, demoted])
    assert sorted(group.telegram_admin_ids) == sorted([payer, roster_only])


@pytest.mark.asyncio
async def test_get_group_costs_the_same_number_of_queries(patched_db_conn, clean_db):
    """The roster must not add a per-message round trip.

    `get_group` runs on every message. The plan's acceptance note said "exactly
    two queries"; the real pre-existing cost is three (groups, administrators,
    approved_members) and it stays three — what matters is that the roster is
    read by the SAME statement, not by a new one.
    """
    group_id = 880102
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id) VALUES ($1) ON CONFLICT DO NOTHING",
            group_id,
        )

    class _CountingConn:
        def __init__(self, conn):
            self._conn = conn
            self.queries: list[str] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def fetch(self, query, *args):
            self.queries.append(query)
            return await self._conn.fetch(query, *args)

        async def fetchrow(self, query, *args):
            self.queries.append(query)
            return await self._conn.fetchrow(query, *args)

        async def fetchval(self, query, *args):
            self.queries.append(query)
            return await self._conn.fetchval(query, *args)

        async def execute(self, query, *args):
            self.queries.append(query)
            return await self._conn.execute(query, *args)

    counting = _CountingConn(clean_db.acquire())

    class _Pool:
        def acquire(self):
            return counting

    with patch(
        "app.database.group_operations.get_pool", AsyncMock(return_value=_Pool())
    ):
        await get_group(group_id)

    assert len(counting.queries) == 3
    assert (
        sum("group_administrators" in query for query in counting.queries) == 1
    )


# --- retire_group: observation, not cleanup (D1/D7) -------------------------


async def _seed_retirable(clean_db, group_id: int, admin_id: int) -> None:
    """An active group with one roster seat and one approved member."""
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, status) VALUES ($1, 'Retirable', 'active')",
            group_id,
        )
        await conn.execute("INSERT INTO administrators (admin_id) VALUES ($1)", admin_id)
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id, is_registered) "
            "VALUES ($1, $2, 0)",
            group_id,
            admin_id,
        )
        await conn.execute(
            "INSERT INTO approved_members (group_id, member_id) VALUES ($1, $2)",
            group_id,
            admin_id,
        )


@pytest.mark.asyncio
async def test_retire_group_is_idempotent_and_emits_one_event(
    patched_db_conn, clean_db
):
    """Second retirement is a no-op: no second event, no moved stamp (D7)."""
    group_id, admin_id = -1009101, 9101
    await _seed_retirable(clean_db, group_id, admin_id)

    assert await retire_group(group_id, reason="sweep_inaccessible") is True

    async with clean_db.acquire() as conn:
        first = await conn.fetchrow(
            "SELECT status, bot_removed_at FROM groups WHERE group_id = $1", group_id
        )

    assert await retire_group(group_id, reason="sweep_inaccessible") is False

    async with clean_db.acquire() as conn:
        after = await conn.fetchrow(
            "SELECT status, bot_removed_at FROM groups WHERE group_id = $1", group_id
        )
        events = await conn.fetch(
            "SELECT action, reason FROM entity_events "
            "WHERE entity_type = 'group' AND entity_id = $1",
            group_id,
        )

    assert first["status"] == "left"
    assert first["bot_removed_at"] is not None  # the retire stamps, it does not just flip status
    assert after["status"] == "left"
    # The stamp is COALESCE'd, so a re-retire cannot move it.
    assert after["bot_removed_at"] == first["bot_removed_at"]
    assert len(events) == 1
    assert events[0]["action"] == "group_retired"
    assert events[0]["reason"] == "sweep_inaccessible"


@pytest.mark.asyncio
async def test_retire_group_refuses_a_missing_group(patched_db_conn, clean_db):
    """A group with no row has nothing to retire: False, and no event."""
    missing = -1009102

    assert await retire_group(missing, reason="sweep_inaccessible") is False

    async with clean_db.acquire() as conn:
        events = await conn.fetch(
            "SELECT 1 FROM entity_events WHERE entity_id = $1", missing
        )
        row = await conn.fetchrow(
            "SELECT 1 FROM groups WHERE group_id = $1", missing
        )
    assert events == []
    assert row is None


@pytest.mark.asyncio
async def test_retire_group_keeps_the_roster(patched_db_conn, clean_db):
    """D1: the paying-admin link survives — this is NOT cleanup_group_data."""
    group_id, admin_id = -1009103, 9103
    await _seed_retirable(clean_db, group_id, admin_id)

    assert await retire_group(group_id, reason="sweep_inaccessible") is True

    async with clean_db.acquire() as conn:
        seats = await conn.fetch(
            "SELECT admin_id, is_registered FROM group_administrators WHERE group_id = $1",
            group_id,
        )
        members = await conn.fetch(
            "SELECT member_id FROM approved_members WHERE group_id = $1", group_id
        )
        # last_active is message activity, not lifecycle: it must not move (D5)
        row = await conn.fetchrow(
            "SELECT status, last_active, created_at FROM groups WHERE group_id = $1",
            group_id,
        )

    assert len(seats) == 1
    assert seats[0]["admin_id"] == admin_id
    assert bool(seats[0]["is_registered"]) is False  # billing flag preserved
    assert [m["member_id"] for m in members] == [admin_id]
    assert row["status"] == "left"


# --- migrate_group: identity + roster, never the caches (D3) ----------------


@pytest.mark.asyncio
async def test_migrate_group_moves_roster_and_retires_the_old_row(
    patched_db_conn, clean_db
):
    """The upgrade: new row carries the old lifecycle, roster moves, old retires."""
    old_id, new_id, admin_id = -1009201, -1003721264646, 9201

    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, username, moderation_enabled, "
            "status, bot_added_at) "
            "VALUES ($1, 'Old', 'oldchan', 1, 'active', '2026-10-01 12:00:00')",
            old_id,
        )
        await conn.execute("INSERT INTO administrators (admin_id) VALUES ($1)", admin_id)
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id, is_registered) "
            "VALUES ($1, $2, 1)",
            old_id,
            admin_id,
        )
        await conn.execute(
            "INSERT INTO approved_members (group_id, member_id, trust_source) "
            "VALUES ($1, $2, 'pre_existing')",
            old_id,
            admin_id,
        )

    assert await migrate_group(old_id, new_id) is True

    async with clean_db.acquire() as conn:
        new_row = await conn.fetchrow(
            "SELECT * FROM groups WHERE group_id = $1", new_id
        )
        old_row = await conn.fetchrow(
            "SELECT * FROM groups WHERE group_id = $1", old_id
        )
        new_seats = await conn.fetch(
            "SELECT admin_id, is_registered FROM group_administrators WHERE group_id = $1",
            new_id,
        )
        old_seats = await conn.fetch(
            "SELECT admin_id FROM group_administrators WHERE group_id = $1", old_id
        )
        new_members = await conn.fetch(
            "SELECT member_id, trust_source FROM approved_members WHERE group_id = $1",
            new_id,
        )

    assert new_row is not None
    assert new_row["title"] == "Old"
    assert new_row["username"] == "oldchan"
    assert bool(new_row["moderation_enabled"]) is True
    # Q2: verbatim, so neither NULL (inference dead) nor NOW() (fresh 72 h)
    assert new_row["bot_added_at"] is not None
    assert new_row["bot_added_at"] == old_row["bot_added_at"]
    # D3: rights at the destination are unprobed, so the grace clock starts clean
    assert new_row["no_rights_detected_at"] is None

    assert old_row["status"] == "left"
    assert old_row["migrated_to_group_id"] == new_id

    assert [s["admin_id"] for s in new_seats] == [admin_id]
    assert bool(new_seats[0]["is_registered"]) is True  # billing link preserved
    assert [s["admin_id"] for s in old_seats] == [admin_id]  # old roster untouched
    assert [m["member_id"] for m in new_members] == [admin_id]
    assert new_members[0]["trust_source"] == "pre_existing"


@pytest.mark.asyncio
async def test_migrate_group_does_not_clobber_an_existing_destination_row(
    patched_db_conn, clean_db
):
    """D3: a row already at the destination keeps its state; seats are still added."""
    old_id, new_id, admin_id = -1009202, -1003721264647, 9202

    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, moderation_enabled, status) "
            "VALUES ($1, 'Old', 1, 'active')",
            old_id,
        )
        await conn.execute(
            "INSERT INTO groups (group_id, title, moderation_enabled, status) "
            "VALUES ($1, 'dest', 0, 'paused')",
            new_id,
        )
        await conn.execute("INSERT INTO administrators (admin_id) VALUES ($1)", admin_id)
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id, is_registered) "
            "VALUES ($1, $2, 1)",
            old_id,
            admin_id,
        )

    assert await migrate_group(old_id, new_id) is True

    async with clean_db.acquire() as conn:
        dest = await conn.fetchrow(
            "SELECT title, status, moderation_enabled FROM groups WHERE group_id = $1",
            new_id,
        )
        seats = await conn.fetch(
            "SELECT admin_id FROM group_administrators WHERE group_id = $1", new_id
        )

    # The destination's own lifecycle wins — this is the guard against DO UPDATE
    assert dest["title"] == "dest"
    assert dest["status"] == "paused"
    assert bool(dest["moderation_enabled"]) is False
    # ...but the missing seat is still added
    assert [s["admin_id"] for s in seats] == [admin_id]


@pytest.mark.asyncio
async def test_bot_add_grants_and_records_the_grant_once(patched_db_conn, clean_db):
    """The bot-add branch pays 100 credits AND leaves the grant record behind.

    Recording it is what stops the double grant. `initialize_new_admin` keys on
    `activated_at`, so a bot-add that paid without stamping it let the admin's
    first `/start` add a SECOND `INITIAL_CREDITS` — 200 against one grant. The
    boot backfill used to hide that; it is a migration step now, so the record
    has to be real.

    The second call is the control: a re-add of the same group must neither pay
    again nor overwrite a balance the admin topped up.
    """
    from app.database.constants import INITIAL_CREDITS

    group_id, admin_id = 880101, 880111
    await update_group_admins(group_id, [admin_id], ["fresh"])

    async with clean_db.acquire() as conn:
        first = await conn.fetchrow(
            "SELECT credits, activated_at FROM administrators WHERE admin_id = $1",
            admin_id,
        )
        grants = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions "
            "WHERE admin_id = $1 AND type = 'initial'",
            admin_id,
        )

        assert first["credits"] == INITIAL_CREDITS
        assert first["activated_at"] is not None, "the grant was not recorded"
        assert grants == 1

        # A top-up between the two adds must survive the second one.
        await conn.execute(
            "UPDATE administrators SET credits = 150 WHERE admin_id = $1", admin_id
        )

    await update_group_admins(group_id, [admin_id], ["fresh"])

    async with clean_db.acquire() as conn:
        second = await conn.fetchrow(
            "SELECT credits, activated_at FROM administrators WHERE admin_id = $1",
            admin_id,
        )
        grants = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions "
            "WHERE admin_id = $1 AND type = 'initial'",
            admin_id,
        )

    assert second["credits"] == 150, "the re-add overwrote a topped-up balance"
    assert second["activated_at"] is not None
    assert grants == 1, "the re-add paid a second initial grant"


@pytest.mark.asyncio
async def test_upsert_group_admin_seeds_registered_from_the_holders_record(
    patched_db_conn, clean_db
):
    """A discovered seat is billing iff its holder already proved reachability.

    Three positive proofs, one per row, and one bare stranger as the control:
    a NULL-`activated_at`, 0-credit, transaction-less holder is a stranger
    discovered in an admin list, and seeding them TRUE would make a person who
    never opened the bot a billing contact.
    """
    group_id = 880102
    activated, funded, granted, stranger = 880121, 880122, 880123, 880124

    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO groups (group_id) VALUES ($1)", group_id)
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits, activated_at) "
            "VALUES ($1, 0, NOW())",
            activated,
        )
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits) VALUES ($1, 40)", funded
        )
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits) VALUES ($1, 0)", granted
        )
        await conn.execute(
            "INSERT INTO transactions (admin_id, amount, type) VALUES ($1, 100, 'initial')",
            granted,
        )
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits) VALUES ($1, 0)", stranger
        )

        for admin_id in (activated, funded, granted, stranger):
            await upsert_group_admin(conn, group_id, admin_id, True)

        rows = await conn.fetch(
            "SELECT admin_id, is_registered FROM group_administrators "
            "WHERE group_id = $1",
            group_id,
        )

    seeded = {row["admin_id"]: bool(row["is_registered"]) for row in rows}
    assert seeded[activated] is True, "an activated holder's seat stayed roster-only"
    assert seeded[funded] is True, "a funded holder's seat stayed roster-only"
    assert seeded[granted] is True, "a grant-record holder's seat stayed roster-only"
    assert seeded[stranger] is False, "a stranger was seeded as a billing contact"


@pytest.mark.asyncio
async def test_upsert_group_admin_never_flips_a_registered_seat_back(
    patched_db_conn, clean_db
):
    """Demotion (`is_admin_now` FALSE) must not unregister a paying seat."""
    group_id, admin_id = 880103, 880131

    async with clean_db.acquire() as conn:
        await _seed_group_and_admin(conn, group_id, admin_id)
        await conn.execute(
            "UPDATE administrators SET activated_at = NOW() WHERE admin_id = $1",
            admin_id,
        )
        await upsert_group_admin(conn, group_id, admin_id, True)

        await upsert_group_admin(conn, group_id, admin_id, False)
        await upsert_group_admin(conn, group_id, admin_id, True)

        row = await conn.fetchrow(
            "SELECT is_registered, is_admin_now FROM group_administrators "
            "WHERE group_id = $1 AND admin_id = $2",
            group_id,
            admin_id,
        )

    assert bool(row["is_registered"]) is True
    assert bool(row["is_admin_now"]) is True


# ---------------------------------------------------------------------------
# Billing predicate — a seat that left must never be the payer (#140)
#
# `is_registered` alone was the billing link, so an admin who left the group
# stayed chargeable: measured live 2026-11-09, 2 such seats, one of them
# funding a group it left weeks earlier (last deduction 2026-11-08). The
# predicate now also requires `is_admin_now`, and it is ONE constant so the
# five billing sites cannot drift apart again — see `roster_predicates`.
# ---------------------------------------------------------------------------


async def _seed_seat(
    conn,
    group_id: int,
    admin_id: int,
    *,
    credits: int,
    registered: bool,
    admin_now: bool,
    moderation: bool = True,
) -> None:
    """One administrator row + one roster seat with both flags named."""
    await conn.execute(
        "INSERT INTO groups (group_id, moderation_enabled) VALUES ($1, $2) "
        "ON CONFLICT DO NOTHING",
        group_id,
        1 if moderation else 0,
    )
    await conn.execute(
        "INSERT INTO administrators (admin_id, credits) VALUES ($1, $2) "
        "ON CONFLICT DO NOTHING",
        admin_id,
        credits,
    )
    await conn.execute(
        "INSERT INTO group_administrators "
        "(group_id, admin_id, is_registered, is_admin_now) VALUES ($1, $2, $3, $4)",
        group_id,
        admin_id,
        1 if registered else 0,
        1 if admin_now else 0,
    )


@pytest.mark.asyncio
async def test_deduction_skips_a_departed_admin_and_charges_the_current_one(
    patched_db_conn, clean_db
):
    """The departed seat holds the biggest balance and must still not pay.

    This is the exact live shape: `meoww_meow_meeeow` (85 credits, left the
    group) outranked the sitting admin, so the group was funded by somebody who
    could not see its `/stats` and had not been a member for weeks.
    """
    group_id = 881001
    departed, current = 881011, 881012

    async with clean_db.acquire() as conn:
        await _seed_seat(
            conn, group_id, departed, credits=500, registered=True, admin_now=False
        )
        await _seed_seat(
            conn, group_id, current, credits=10, registered=True, admin_now=True
        )

    assert await deduct_credits_from_admins(group_id, 1) == current

    async with clean_db.acquire() as conn:
        balances = dict(
            await conn.fetch(
                "SELECT admin_id, credits FROM administrators "
                "WHERE admin_id IN ($1, $2)",
                departed,
                current,
            )
        )
    assert balances[departed] == 500, "the departed admin was charged"
    assert balances[current] == 9


@pytest.mark.asyncio
async def test_deduction_refuses_when_only_a_departed_admin_has_credits(
    patched_db_conn, clean_db
):
    """No billable seat means no charge — 0 is the insufficient-credits signal.

    Deliberate: `try_deduct_credits` reads 0 as "cannot pay" and deactivates the
    group, which is the honest outcome. Charging the departed admin instead
    would keep a group alive on money from somebody who left.
    """
    group_id = 881002
    departed = 881021

    async with clean_db.acquire() as conn:
        await _seed_seat(
            conn, group_id, departed, credits=500, registered=True, admin_now=False
        )

    assert await deduct_credits_from_admins(group_id, 1) == 0

    async with clean_db.acquire() as conn:
        credits = await conn.fetchval(
            "SELECT credits FROM administrators WHERE admin_id = $1", departed
        )
    assert credits == 500


@pytest.mark.asyncio
async def test_get_paying_admins_excludes_a_departed_admin(patched_db_conn, clean_db):
    """Low-balance warnings must not chase somebody who left the group."""
    group_id = 881003
    departed, current = 881031, 881032

    async with clean_db.acquire() as conn:
        await _seed_seat(
            conn, group_id, departed, credits=500, registered=True, admin_now=False
        )
        await _seed_seat(
            conn, group_id, current, credits=5, registered=True, admin_now=True
        )
        # A roster-only stranger: never opened the bot, so never a payer.
        await _seed_seat(
            conn, group_id, 881033, credits=900, registered=False, admin_now=True
        )

    assert await get_paying_admins(group_id) == [current]


@pytest.mark.asyncio
async def test_paid_groups_query_ignores_a_departed_payer(patched_db_conn, clean_db):
    """The silent-death scan must not call a group funded by an absent admin.

    `get_paid_groups_with_moderation_off` is what reports "you are paying but
    nothing is moderated". A departed payer would make that report fire for
    somebody who cannot act on it, and would hide the real state (nobody can
    fund the group) behind a false one.
    """
    from app.database.group_operations import get_paid_groups_with_moderation_off

    group_id = 881004
    departed, current = 881041, 881042

    async with clean_db.acquire() as conn:
        await _seed_seat(
            conn,
            group_id,
            departed,
            credits=500,
            registered=True,
            admin_now=False,
            moderation=False,
        )

    assert group_id not in [
        row["group_id"]
        for row in await get_paid_groups_with_moderation_off()
    ], "a departed admin's balance was read as funding"

    # The same group with a SITTING admin funded becomes a real finding: the
    # negative above is a property of the predicate, not of the group.
    async with clean_db.acquire() as conn:
        await _seed_seat(
            conn,
            group_id,
            current,
            credits=5,
            registered=True,
            admin_now=True,
            moderation=False,
        )

    assert group_id in [
        row["group_id"]
        for row in await get_paid_groups_with_moderation_off()
    ]


def test_every_billing_site_composes_the_one_shared_predicate():
    """The anti-drift guard: five sites, one constant, asserted by source.

    The defect this closes was DRIFT, not duplication — `recover_failed`'s twin
    documented itself as "same join, same ordering" while carrying neither flag.
    A behaviour test cannot catch that (the twin's behaviour was tested; it was
    the promise in its docstring that was false), so this asserts the shape:
    every billing site must interpolate the constant, never spell the predicate
    out again.
    """
    import inspect

    from app.database import group_operations, roster_predicates

    sites = {
        "deduct_credits_from_admins": group_operations.deduct_credits_from_admins,
        "get_paying_admins": group_operations.get_paying_admins,
        "get_paid_groups_with_moderation_off": (
            group_operations.get_paid_groups_with_moderation_off
        ),
    }
    for name, func in sites.items():
        source = inspect.getsource(func)
        assert "BILLABLE_SEAT" in source or "PAYING_SEAT" in source, (
            f"{name} restates the billing predicate instead of composing it"
        )
        assert "is_registered = TRUE" not in source, (
            f"{name} still carries its own copy of the predicate"
        )

    # The two fragments themselves carry the flags the whole leg is about.
    assert "is_admin_now = TRUE" in roster_predicates.BILLABLE_SEAT
    assert "is_admin_now = TRUE" in roster_predicates.PAYING_SEAT
    assert "is_admin_now" not in roster_predicates.REGISTERED_SEAT, (
        "the ownership predicate must fail OPEN — see roster_predicates"
    )


@pytest.mark.asyncio
async def test_pause_group_preserving_data_keeps_mappings(patched_db_conn, clean_db):
    """#145 close condition 5: the hold pauses WITHOUT destroying mappings.

    SV CLUB (the protected channel) and SV CHAT (its discussion group, 58
    approved members) are seeded as they are live. Pausing the channel must
    leave every mapping row in place and must NOT stamp `bot_removed_at`, since
    the bot never leaves.

    The second half is the positive control: `cleanup_group_data` — the path the
    first version of this guard used — deletes exactly these rows. A test that
    could not observe that destruction would be certifying nothing.
    """
    sv_club = -1002107631154
    sv_chat = -1002021099836
    admin_id = 882155584
    approved = 58

    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO administrators (admin_id, username, credits) VALUES ($1, 'sv', 67)",
            admin_id,
        )
        # Each $N used ONCE: the SQLite test adapter rewrites $N -> ? in text
        # order, so a reused $1 becomes two placeholders for one argument.
        await conn.execute(
            """
            INSERT INTO groups (group_id, title, moderation_enabled, status, linked_channel_id)
            VALUES ($1, 'SV CLUB', TRUE, 'active', $2),
                   ($3, 'SV CHAT', TRUE, 'active', $4)
            """,
            sv_club,
            sv_chat,
            sv_chat,
            sv_club,
        )
        # Both rows carry a roster row live (HQ, 2026-11-09): SV CLUB's is the
        # 67-credit admin, SV CHAT's is the discussion-group mapping.
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2), ($3, $4)",
            sv_club,
            admin_id,
            sv_chat,
            admin_id,
        )
        for member_id in range(1, approved + 1):
            await conn.execute(
                "INSERT INTO approved_members (group_id, member_id) VALUES ($1, $2)",
                sv_chat,
                member_id,
            )

    changed = await pause_group_preserving_data(
        sv_club, reason="no_rights_protected_channel"
    )
    assert changed is True

    async with clean_db.acquire() as conn:
        club = await conn.fetchrow(
            "SELECT status, moderation_enabled, bot_removed_at FROM groups WHERE group_id = $1",
            sv_club,
        )
        chat = await conn.fetchrow(
            "SELECT status, moderation_enabled, bot_removed_at FROM groups WHERE group_id = $1",
            sv_chat,
        )
        club_roster = await conn.fetchval(
            "SELECT count(*) FROM group_administrators WHERE group_id = $1", sv_club
        )
        roster = await conn.fetchval(
            "SELECT count(*) FROM group_administrators WHERE group_id = $1", sv_chat
        )
        members = await conn.fetchval(
            "SELECT count(*) FROM approved_members WHERE group_id = $1", sv_chat
        )
        event = await conn.fetchrow(
            "SELECT action, reason FROM entity_events WHERE entity_id = $1", sv_club
        )

    # The channel is paused, but not retired and not stripped.
    assert club["status"] == "paused"
    assert club["bot_removed_at"] is None, "a hold must not record a leave"
    # The discussion group is untouched in every respect.
    assert chat["status"] == "active"
    # bool(): asyncpg returns True, the SQLite test adapter returns 1.
    assert bool(chat["moderation_enabled"]) is True
    assert chat["bot_removed_at"] is None
    assert club_roster == 1, "the channel's own roster row was destroyed by the pause"
    assert roster == 1, "the discussion group's roster row was destroyed by the pause"
    assert members == approved, "approved members destroyed by the pause"
    assert event["action"] == "group_paused"
    assert event["reason"] == "no_rights_protected_channel"

    # Idempotent: a second pause changes nothing and writes no second event.
    assert await pause_group_preserving_data(sv_club, reason="again") is False
    async with clean_db.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM entity_events WHERE entity_id = $1", sv_club
            )
            == 1
        )

    # POSITIVE CONTROL: the destructive sibling DOES remove both mapping sets,
    # so the assertions above are capable of failing.
    await cleanup_group_data(sv_chat, status="paused", reason="control")
    async with clean_db.acquire() as conn:
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM approved_members WHERE group_id = $1", sv_chat
            )
            == 0
        )
        assert (
            await conn.fetchval(
                "SELECT count(*) FROM group_administrators WHERE group_id = $1", sv_chat
            )
            == 0
        )

