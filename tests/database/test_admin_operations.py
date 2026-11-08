import pytest

from app.database import (
    count_groups_awaiting_moderation_restore,
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


@pytest.mark.asyncio
async def test_count_groups_awaiting_moderation_restore(patched_db_conn, clean_db):
    """Issue #41: the payment restore joins `group_administrators`, which the
    low-balance leave HARD-DELETES — so a top-up made before the bot is re-added
    matches zero groups and restores nothing. This count is what the payment
    handler reports, so a zero is visible instead of silent.
    """
    admin_id = 8101
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits) VALUES ($1, $2)",
            admin_id,
            0,
        )
        # Moderation off + a mapping -> the payment restores this one.
        await conn.execute(
            "INSERT INTO groups (group_id, title, moderation_enabled) "
            "VALUES ($1, 'Restorable', 0)",
            -3001,
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            -3001,
            admin_id,
        )
        # Already moderated -> nothing to restore.
        await conn.execute(
            "INSERT INTO groups (group_id, title, moderation_enabled) "
            "VALUES ($1, 'Already on', 1)",
            -3002,
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            -3002,
            admin_id,
        )
        # Another admin's group with moderation off -> must NOT be counted.
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits) VALUES ($1, $2)", 8102, 0
        )
        await conn.execute(
            "INSERT INTO groups (group_id, title, moderation_enabled) "
            "VALUES ($1, 'Someone else', 0)",
            -3003,
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            -3003,
            8102,
        )

    assert await count_groups_awaiting_moderation_restore(admin_id) == 1

    # The post-leave state: the mapping is gone, so a top-up restores NOTHING.
    # This is the zero the payment handler now reports instead of staying silent.
    async with clean_db.acquire() as conn:
        await conn.execute(
            "DELETE FROM group_administrators WHERE admin_id = $1", admin_id
        )

    assert await count_groups_awaiting_moderation_restore(admin_id) == 0

@pytest.mark.asyncio
async def test_describe_moderation_restore_keeps_the_three_states_apart(
    patched_db_conn, clean_db
):
    """A zero `restored` means three different things; they must not collapse.

    Measured live 2026-11-08: admin 1457796755 paid 100 stars, moderation was
    already ON in their only group (100 deductions before the payment), and the
    old code reported `restored == 0` and warned that the join "matched nothing"
    and to re-add the bot — both stated causes false. Fleet-wide, 35 of the 37
    admins with groups are in that healthy-no-op state, so the collapsed number
    fired a false alarm for 94.6% of the population it was aimed at.

    The three states, each asserted separately so a future refactor that merges
    any two of them fails by name:
      * moderation OFF  -> `restored` counts it (the good path)
      * moderation ON   -> `already_on` counts it (success, not a warning)
      * no mapping      -> `registered_groups == 0` (the only real issue #41)
    """
    from app.database.admin_operations import describe_moderation_restore

    healthy_id = 8201  # mapping present, moderation already on (the false-alarm case)
    flipping_id = 8202  # mapping present, moderation off (the good path)
    orphan_id = 8203  # no mapping at all (the genuine issue #41 case)

    async with clean_db.acquire() as conn:
        for aid in (healthy_id, flipping_id, orphan_id):
            await conn.execute(
                "INSERT INTO administrators (admin_id, credits) VALUES ($1, $2)",
                aid,
                0,
            )
        await conn.execute(
            "INSERT INTO groups (group_id, title, moderation_enabled) "
            "VALUES ($1, 'Already on', 1)",
            -4001,
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            -4001,
            healthy_id,
        )
        await conn.execute(
            "INSERT INTO groups (group_id, title, moderation_enabled) "
            "VALUES ($1, 'Off, restorable', 0)",
            -4002,
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            -4002,
            flipping_id,
        )

    # STATE 2 — the good path: exactly one flip, nothing already-on.
    flipping = await describe_moderation_restore(flipping_id)
    assert (flipping.restored, flipping.already_on, flipping.registered_groups) == (
        1,
        0,
        1,
    ), flipping
    assert flipping.has_mapping is True

    # STATE 1 — the false-alarm case: zero to restore, but the mapping IS there.
    # `restored == 0` alone cannot distinguish this from STATE 3; `has_mapping`
    # is what makes the distinction, and this assertion is the guard on it.
    healthy = await describe_moderation_restore(healthy_id)
    assert (healthy.restored, healthy.already_on, healthy.registered_groups) == (
        0,
        1,
        1,
    ), healthy
    assert healthy.has_mapping is True

    # STATE 3 — the genuine issue #41 case: nothing to restore because the
    # low-balance leave hard-deleted the mapping. The ONLY state that warrants
    # the "re-add the bot" warning.
    orphan = await describe_moderation_restore(orphan_id)
    assert (orphan.restored, orphan.already_on, orphan.registered_groups) == (
        0,
        0,
        0,
    ), orphan
    assert orphan.has_mapping is False

    # The log line carries all three numbers, so a future incident can be read
    # out of the log instead of reconstructed from the database.
    assert healthy.as_log_line() == (
        "restored=0 already_on=1 registered_groups=1"
    )

    # And the count helper stays the `restored` arm, not the collapsed sum.
    assert await count_groups_awaiting_moderation_restore(healthy_id) == 0
    assert await count_groups_awaiting_moderation_restore(flipping_id) == 1


# --- #130: activation is keyed on activated_at, never on row existence --------
#
# The admin roster prefill creates an `administrators` row (0 credits,
# `activated_at` NULL) for every Telegram admin it discovers. Keying the
# welcome on "does a row exist" would have denied the welcome and the initial
# credits to every prefilled admin — silently, and only for the 48 users the
# prefill exists to serve.


async def _seed_prefilled_admin(clean_db, admin_id: int) -> None:
    """The exact shape the roster prefill writes: row present, never active."""
    from app.database.group_operations import seed_administrator_row

    async with clean_db.acquire() as conn:
        await seed_administrator_row(conn, admin_id)


async def _activation_state(clean_db, admin_id: int):
    async with clean_db.acquire() as conn:
        activated_at = await conn.fetchval(
            "SELECT activated_at FROM administrators WHERE admin_id = $1", admin_id
        )
        credits = await conn.fetchval(
            "SELECT credits FROM administrators WHERE admin_id = $1", admin_id
        )
        initials = await conn.fetchval(
            "SELECT COUNT(*) FROM transactions "
            "WHERE admin_id = $1 AND type = 'initial'",
            admin_id,
        )
    return activated_at, credits, initials


@pytest.mark.asyncio
async def test_initialize_new_admin_grants_prefilled_roster_row(patched_db_conn, clean_db):
    """A row created by the roster prefill still gets its welcome."""
    admin_id = 789101
    await _seed_prefilled_admin(clean_db, admin_id)

    activated_at, credits, initials = await _activation_state(clean_db, admin_id)
    assert activated_at is None, "prefill must not activate"
    assert credits == 0
    assert initials == 0

    async with clean_db.acquire() as conn:
        assert await initialize_new_admin(admin_id) is True

    activated_at, credits, initials = await _activation_state(clean_db, admin_id)
    assert activated_at is not None, "the welcome must stamp activation"
    assert credits == INITIAL_CREDITS
    assert initials == 1


@pytest.mark.asyncio
async def test_initialize_new_admin_second_start_is_noop(patched_db_conn, clean_db):
    """`/start` twice grants once — no second 100 credits, no second row."""
    admin_id = 789102
    await _seed_prefilled_admin(clean_db, admin_id)

    async with clean_db.acquire() as conn:
        assert await initialize_new_admin(admin_id) is True
        assert await initialize_new_admin(admin_id) is False

    _, credits, initials = await _activation_state(clean_db, admin_id)
    assert credits == INITIAL_CREDITS
    assert initials == 1


@pytest.mark.asyncio
async def test_initialize_new_admin_activated_row_grants_nothing(patched_db_conn, clean_db):
    """An already-activated admin who `/start`s again gets nothing."""
    admin_id = 789103
    await _seed_prefilled_admin(clean_db, admin_id)

    async with clean_db.acquire() as conn:
        await initialize_new_admin(admin_id)
        # Simulate a balance they bought; a later /start must not touch it.
        await conn.execute(
            "UPDATE administrators SET credits = 55 WHERE admin_id = $1", admin_id
        )

    async with clean_db.acquire() as conn:
        assert await initialize_new_admin(admin_id) is False

    _, credits, initials = await _activation_state(clean_db, admin_id)
    assert credits == 55, "a repeat /start must not reset or top up a balance"
    assert initials == 1


@pytest.mark.asyncio
async def test_initialize_new_admin_preserves_existing_balance(patched_db_conn, clean_db):
    """Activation is additive: a row that somehow carries credits keeps them."""
    admin_id = 789104
    await _seed_prefilled_admin(clean_db, admin_id)

    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE administrators SET credits = 7 WHERE admin_id = $1", admin_id
        )
        assert await initialize_new_admin(admin_id) is True

    _, credits, initials = await _activation_state(clean_db, admin_id)
    assert credits == 7 + INITIAL_CREDITS
    assert initials == 1
