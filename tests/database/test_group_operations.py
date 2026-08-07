import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.database import (
    Administrator,
    add_member,
    clear_no_rights_detected_at,
    deduct_credits_from_admins,
    get_admin_group_ids,
    get_admin_groups,
    get_groups_with_no_rights_past_grace,
    get_moderation_event_count,
    get_paying_admins,
    increment_moderation_events,
    is_member_in_group,
    is_moderation_enabled,
    is_trusted_member,
    remove_member_from_group,
    set_group_moderation,
    update_group_admins,
    set_moderation_events,
    set_no_rights_detected_at,
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
        await conn.execute("INSERT INTO groups (group_id, title) VALUES ($1, 'Stale')", group_id)
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
        TelegramBadRequest(
            method=MagicMock(), message="Bad Request: chat not found"
        ),
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

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM groups WHERE group_id = $1", group_id)
        assert row is None


@pytest.mark.asyncio
async def test_get_admin_groups_unexpected_telegram_error_logs_error(
    patched_db_conn, clean_db, caplog
):
    """Unexpected Telegram errors still log at ERROR without DB cleanup."""
    admin_id = 9002
    group_id = -1009002
    await _seed_admin_group(clean_db, admin_id, group_id)
    err = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: invalid chat id"
    )

    with patch("app.database.group_operations.bot") as mock_bot:
        mock_bot.get_chat = AsyncMock(side_effect=err)
        with caplog.at_level(logging.ERROR, logger="app.database.group_operations"):
            groups = await get_admin_groups(admin_id)

    assert groups == []
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    assert not any("inaccessible during admin stats" in r.message for r in caplog.records)

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM groups WHERE group_id = $1", group_id)
        assert row is not None


@pytest.mark.asyncio
async def test_get_admin_groups_returns_accessible_chats(patched_db_conn, clean_db):
    """Accessible groups are returned with live Telegram title."""
    admin_id = 9003
    group_id = -1009003
    await _seed_admin_group(clean_db, admin_id, group_id)

    chat = MagicMock(title="Live Group")
    with patch("app.database.group_operations.bot") as mock_bot:
        mock_bot.get_chat = AsyncMock(return_value=chat)
        groups = await get_admin_groups(admin_id)

    assert len(groups) == 1
    assert groups[0]["id"] == group_id
    assert groups[0]["title"] == "Live Group"


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
async def test_update_group_admins_excludes_group_anonymous_bot(patched_db_conn, clean_db):
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
async def test_activate_discussion_group_flips_awaiting_rights(patched_db_conn, clean_db):
    """Awaiting-rights row (linked_channel_id set, moderation off) flips active."""
    from app.database import upsert_awaiting_rights_group, activate_discussion_group

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
async def test_activate_discussion_group_leaves_disabled_group_alone(patched_db_conn, clean_db):
    """A group without linked_channel_id (deliberately disabled) is untouched."""
    from app.database import set_group_moderation, activate_discussion_group

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
async def test_activate_discussion_group_active_with_channel_stays_active(patched_db_conn, clean_db):
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
