import pytest

from app.database import (
    cycle_moderation_mode,
    get_admin,
    get_admin_credits,
    get_admin_stats,
    get_moderation_mode,
    get_spent_credits_last_week,
    initialize_new_admin,
    save_admin,
)
from app.database.constants import INITIAL_CREDITS
from app.database.models import ModerationMode


@pytest.mark.asyncio
async def test_save_and_get_user(patched_db_conn, clean_db, sample_user):
    """Test saving and retrieving a user"""
    async with clean_db.acquire() as conn:
        await save_admin(sample_user)
        retrieved_user = await get_admin(sample_user.admin_id)

    assert retrieved_user is not None
    assert retrieved_user.admin_id == sample_user.admin_id
    assert retrieved_user.username == sample_user.username
    assert retrieved_user.credits == sample_user.credits
    assert retrieved_user.moderation_mode == sample_user.moderation_mode


@pytest.mark.asyncio
async def test_initialize_new_user(patched_db_conn, clean_db):
    """Test initializing a new user"""
    user_id = 789012

    async with clean_db.acquire() as conn:
        result = await initialize_new_admin(user_id)

        assert result is True

        user = await get_admin(user_id)
        assert user is not None
        assert user.credits == INITIAL_CREDITS
        assert user.moderation_mode == ModerationMode.NOTIFY


@pytest.mark.asyncio
async def test_cycle_moderation_mode(patched_db_conn, clean_db, sample_user):
    """Test cycling moderation mode notify → delete → delete_silent → notify"""
    async with clean_db.acquire() as conn:
        await save_admin(sample_user)

        assert await get_moderation_mode(sample_user.admin_id) == ModerationMode.NOTIFY

        assert (
            await cycle_moderation_mode(sample_user.admin_id) == ModerationMode.DELETE
        )
        assert await get_moderation_mode(sample_user.admin_id) == ModerationMode.DELETE

        assert (
            await cycle_moderation_mode(sample_user.admin_id)
            == ModerationMode.DELETE_SILENT
        )
        assert (
            await get_moderation_mode(sample_user.admin_id)
            == ModerationMode.DELETE_SILENT
        )

        assert (
            await cycle_moderation_mode(sample_user.admin_id) == ModerationMode.NOTIFY
        )
        assert await get_moderation_mode(sample_user.admin_id) == ModerationMode.NOTIFY


@pytest.mark.asyncio
async def test_cycle_moderation_mode_non_existent_admin(patched_db_conn, clean_db):
    async with clean_db.acquire() as conn:
        new_mode = await cycle_moderation_mode(999999)
        assert new_mode is None


@pytest.mark.asyncio
async def test_get_moderation_mode_default(patched_db_conn, clean_db):
    """Unknown admin defaults to notify."""
    async with clean_db.acquire() as conn:
        assert await get_moderation_mode(999999) == ModerationMode.NOTIFY


@pytest.mark.asyncio
async def test_dual_write_syncs_delete_spam(patched_db_conn, clean_db, sample_user):
    """When delete_spam column exists, cycle updates legacy boolean."""
    import app.database.admin_operations as admin_ops

    admin_ops._delete_spam_column_exists = None

    async with clean_db.acquire() as conn:
        await conn.execute(
            "ALTER TABLE administrators ADD COLUMN delete_spam BOOLEAN DEFAULT 0"
        )
        admin_ops._delete_spam_column_exists = None
        await save_admin(sample_user)

        await cycle_moderation_mode(sample_user.admin_id)
        row = await conn.fetchrow(
            "SELECT moderation_mode, delete_spam FROM administrators WHERE admin_id = $1",
            sample_user.admin_id,
        )
        assert row["moderation_mode"] == "delete"
        assert bool(row["delete_spam"])

        await cycle_moderation_mode(sample_user.admin_id)
        row = await conn.fetchrow(
            "SELECT moderation_mode, delete_spam FROM administrators WHERE admin_id = $1",
            sample_user.admin_id,
        )
        assert row["moderation_mode"] == "delete_silent"
        assert bool(row["delete_spam"])


@pytest.mark.asyncio
async def test_get_user_credits(patched_db_conn, clean_db, sample_user):
    async with clean_db.acquire() as conn:
        await save_admin(sample_user)
        credits = await get_admin_credits(sample_user.admin_id)
        assert credits == sample_user.credits


@pytest.mark.asyncio
async def test_get_spent_credits_last_week(patched_db_conn, clean_db):
    admin_id = 12345
    group_id = 98765

    async with clean_db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, credits, moderation_mode)
            VALUES ($1, $2, 'notify')
            """,
            admin_id,
            100,
        )

        await conn.execute(
            """
            INSERT INTO groups (group_id)
            VALUES ($1)
            """,
            group_id,
        )

        await conn.execute(
            """
            INSERT INTO group_administrators (group_id, admin_id)
            VALUES ($1, $2)
            """,
            group_id,
            admin_id,
        )

        transactions = [
            (admin_id, -10, "deduct", "Group moderation credit deduction", "NOW()"),
            (
                admin_id,
                -5,
                "deduct",
                "Group moderation credit deduction",
                "NOW() - INTERVAL '3 days'",
            ),
            (
                admin_id,
                -15,
                "deduct",
                "Group moderation credit deduction",
                "NOW() - INTERVAL '6 days'",
            ),
            (admin_id, -25, "payment", "Stars purchase", "NOW()"),
            (
                admin_id,
                -20,
                "deduct",
                "Group moderation credit deduction",
                "NOW() - INTERVAL '8 days'",
            ),
            (admin_id, 50, "deduct", "Group moderation credit deduction", "NOW()"),
        ]

        for tx in transactions:
            await conn.execute(
                f"""
                INSERT INTO transactions (admin_id, amount, type, description, created_at)
                VALUES ($1, $2, $3, $4, {tx[4]})
                """,
                tx[0],
                tx[1],
                tx[2],
                tx[3],
            )

    spent_credits = await get_spent_credits_last_week(admin_id)
    assert spent_credits == 55


@pytest.mark.asyncio
async def test_get_admin_stats_no_groups(patched_db_conn, clean_db):
    admin_id = 123456

    async with clean_db.acquire() as conn:
        await initialize_new_admin(admin_id)
        stats = await get_admin_stats(admin_id)

    assert "global" in stats
    assert "groups" in stats
    assert stats["groups"] == []

    global_stats = stats["global"]
    assert "processed" in global_stats
    assert "spam" in global_stats
    assert "approved" in global_stats
    assert "approved_7d" in global_stats
    assert "spam_examples" in global_stats
    assert "spam_examples_7d" in global_stats
    assert global_stats["processed"] == 0
    assert global_stats["spam"] == 0
    assert global_stats["approved"] == 0
    assert global_stats["approved_7d"] == 0
    assert global_stats["spam_examples"] == 0
    assert global_stats["spam_examples_7d"] == 0


@pytest.mark.asyncio
async def test_get_admin_stats_7d_slices(patched_db_conn, clean_db, monkeypatch):
    """7-day slices count only rows created within the last week."""
    admin_id = 12345
    group_id = 98765

    async with clean_db.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, credits, moderation_mode)
            VALUES ($1, $2, 'notify')
            """,
            admin_id,
            100,
        )
        await conn.execute(
            """
            INSERT INTO groups (group_id)
            VALUES ($1)
            """,
            group_id,
        )
        await conn.execute(
            """
            INSERT INTO group_administrators (group_id, admin_id)
            VALUES ($1, $2)
            """,
            group_id,
            admin_id,
        )
        # 2 approved last week + 1 older.
        await conn.execute(
            """
            INSERT INTO approved_members (group_id, member_id, approved_at)
            VALUES ($1, 1, NOW() - INTERVAL '2 days')
            """,
            group_id,
        )
        await conn.execute(
            """
            INSERT INTO approved_members (group_id, member_id, approved_at)
            VALUES ($1, 2, NOW() - INTERVAL '5 days')
            """,
            group_id,
        )
        await conn.execute(
            """
            INSERT INTO approved_members (group_id, member_id, approved_at)
            VALUES ($1, 3, NOW() - INTERVAL '30 days')
            """,
            group_id,
        )
        # 2 confirmed spam examples last week + 1 older.
        for n in (1, 2):
            await conn.execute(
                """
                INSERT INTO spam_examples (admin_id, text, score, confirmed, created_at)
                VALUES ($1, $2, 90, true, NOW() - INTERVAL '3 days')
                """,
                admin_id,
                f"spam-{n}",
            )
        await conn.execute(
            """
            INSERT INTO spam_examples (admin_id, text, score, confirmed, created_at)
            VALUES ($1, $2, 90, true, NOW() - INTERVAL '40 days')
            """,
            admin_id,
            "old-spam",
        )

    async def fake_get_admin_groups(_admin_id):
        return [
            {
                "id": group_id,
                "title": "Test Group",
                "is_moderation_enabled": True,
                "topic_description_short": None,
                "topic_updated_at": None,
            }
        ]

    monkeypatch.setattr(
        "app.database.group_operations.get_admin_groups",
        fake_get_admin_groups,
    )

    async def fake_get_weekly_stats(_group_ids):
        return {group_id: {"processed": 0, "spam": 0}}

    monkeypatch.setattr(
        "app.common.logfire_lookup.get_weekly_stats",
        fake_get_weekly_stats,
    )

    stats = await get_admin_stats(admin_id)

    global_stats = stats["global"]
    assert global_stats["approved"] == 3  # all time
    assert global_stats["approved_7d"] == 2  # last 7 days only
    assert global_stats["spam_examples"] == 3  # all time
    assert global_stats["spam_examples_7d"] == 2  # last 7 days only

    assert len(stats["groups"]) == 1
    group_stats = stats["groups"][0]
    assert group_stats["approved_users_count"] == 3
    assert group_stats["approved_users_count_7d"] == 2
