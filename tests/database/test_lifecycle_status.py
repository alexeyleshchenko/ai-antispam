"""Tests for the deletion-policy E+C feature.

- Lifecycle status on groups (active / paused / left) replaces hard DELETE.
- Append-only entity_events log records every state transition.
- Re-add reactivates a paused/left group and clears no_rights_detected_at.
- No-rights job + heal target only active groups.
- Pending-spam TTL: 7 days, single batch event row per run.
"""

import json

import pytest

from app.background_jobs import scheduled_tasks
from app.database.group_operations import (
    cleanup_group_data,
    get_groups_with_no_rights_past_grace,
    set_group_moderation,
    update_group_admins,
)
from app.database.models import GroupStatus
from app.database.spam_examples import (
    cleanup_pending_spam_examples,
    insert_pending_spam_example,
)

# ---------- cleanup_group_data ----------


@pytest.mark.asyncio
async def test_cleanup_group_data_paused_status(patched_db_conn, clean_db):
    """cleanup_group_data(paused) flips status, deletes mappings, writes event."""
    group_id = -1001
    admin_id = 9001
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO administrators (admin_id) VALUES ($1)", admin_id
        )
        await conn.execute(
            "INSERT INTO groups (group_id, title) VALUES ($1, 'Stale')", group_id
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            group_id,
            admin_id,
        )
        await conn.execute(
            "INSERT INTO approved_members (group_id, member_id) VALUES ($1, 1234)",
            group_id,
        )

    await cleanup_group_data(
        group_id, status=GroupStatus.PAUSED, reason="low_balance_unpaid"
    )

    async with clean_db.acquire() as conn:
        # Row survives with new status
        row = await conn.fetchrow(
            "SELECT status FROM groups WHERE group_id = $1", group_id
        )
        assert row is not None
        assert row["status"] == "paused"
        # Mappings + approved_members deleted
        ga = await conn.fetchrow(
            "SELECT 1 FROM group_administrators WHERE group_id = $1", group_id
        )
        assert ga is None
        am = await conn.fetchrow(
            "SELECT 1 FROM approved_members WHERE group_id = $1", group_id
        )
        assert am is None
        # Event row written
        ev = await conn.fetchrow(
            "SELECT action, reason, old_row FROM entity_events "
            "WHERE entity_type = 'group' AND entity_id = $1",
            group_id,
        )
        assert ev is not None
        assert ev["action"] == "group_paused"
        assert ev["reason"] == "low_balance_unpaid"


@pytest.mark.asyncio
async def test_cleanup_group_data_old_row_captures_pre_transition_state(
    patched_db_conn, clean_db
):
    """old_row JSON snapshot reflects the row's state BEFORE the UPDATE."""
    group_id = -1002
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, username, moderation_enabled) "
            "VALUES ($1, 'Before', '@pre', 1)",
            group_id,
        )

    await cleanup_group_data(
        group_id, status=GroupStatus.LEFT, reason="inaccessible_chat"
    )

    async with clean_db.acquire() as conn:
        ev = await conn.fetchrow(
            "SELECT old_row FROM entity_events "
            "WHERE entity_type = 'group' AND entity_id = $1",
            group_id,
        )
        assert ev is not None
        # old_row is a JSON string parseable to a dict containing the pre-transition state
        parsed = json.loads(ev["old_row"])
        assert parsed["title"] == "Before"
        assert parsed["username"] == "@pre"
        assert parsed["status"] == "active"  # status before the LEFT transition


# ---------- update_group_admins reactivation ----------


@pytest.mark.asyncio
async def test_update_group_admins_reactivates_paused_group(
    patched_db_conn, clean_db
):
    """Re-add flips paused/left back to active and clears no_rights_detected_at.

    The row assertions below are the whole test, and they are load-bearing: the
    upsert in `update_group_admins` deliberately leaves `status` alone (`DO
    UPDATE` sets only `last_active`/`title`/`username`), so the seeded `paused`
    row survives it and the reactivation branch MUST fire to clear the stamps.

    Before #146 the SQLite adapter rewrote that upsert into INSERT OR REPLACE,
    which reset `status` to the default `active`, skipped the branch under test,
    and let this pass against broken code — which is why it used to neutralize
    the upsert with a spy pool. The rewrite is gone, so nothing is neutralized
    here and the assertions now exercise the production statement itself.
    """
    group_id = -1003
    admin_id = 9003

    async with clean_db.acquire() as conn:
        # D6b: a row retired by the sweep carries bot_removed_at; re-add must
        # clear it, not just the no-rights stamp.
        await conn.execute(
            "INSERT INTO groups (group_id, status, no_rights_detected_at, bot_removed_at) "
            "VALUES ($1, 'paused', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            group_id,
        )
        await conn.execute(
            "INSERT INTO administrators (admin_id) VALUES ($1)", admin_id
        )

    await update_group_admins(group_id, [admin_id], ["admin"], "Reactivated", "@r")

    # Row is active, no_rights_detected_at cleared
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, no_rights_detected_at, bot_removed_at "
            "FROM groups WHERE group_id = $1",
            group_id,
        )
        assert row["status"] == "active"
        assert row["no_rights_detected_at"] is None
        assert row["bot_removed_at"] is None
        # Reactivation event written
        ev = await conn.fetchrow(
            "SELECT action, reason FROM entity_events "
            "WHERE entity_type = 'group' AND entity_id = $1",
            group_id,
        )
        assert ev is not None
        assert ev["action"] == "group_reactivated"


@pytest.mark.asyncio
async def test_readd_after_low_balance_leave_restores_moderation(
    patched_db_conn, clean_db
):
    """Issue #41: pause -> leave -> re-add must end with moderation ON.

    Drives the sequence that left a paying customer's groups silently
    unmoderated for ~36 days. The low-balance leave disables moderation and
    soft-deletes the row; the re-add then reactivated that row WITHOUT
    restoring `moderation_enabled`, so `validation` dropped every message while
    the group read `status=active` — every outward signal healthy.

    This is the #41 regression guard, and it only bites because the row really
    is still paused when the re-add arrives. Before #146 the SQLite adapter
    rewrote the `update_group_admins` upsert into INSERT OR REPLACE, which reset
    `status` to its default `active`, skipped the reactivation branch — the
    branch under test — and let this pass against the unfixed code. It used to
    neutralize that with a spy pool; the rewrite is gone, so the production
    statement runs unmodified.
    """
    group_id = -1006
    admin_id = 9006

    # 1. A live, moderated group with a paying admin.
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits) VALUES ($1, 100)",
            admin_id,
        )
        await conn.execute(
            "INSERT INTO groups (group_id, title, moderation_enabled) "
            "VALUES ($1, 'Paying customer group', 1)",
            group_id,
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            group_id,
            admin_id,
        )

    # 2. Credits run out: moderation is disabled, then the leave soft-deletes the
    #    row and HARD-deletes the admin mapping (handle_deactivation +
    #    cleanup_group_data via the low-balance path).
    await set_group_moderation(group_id, False, "Paying customer group", None)
    await cleanup_group_data(
        group_id, status=GroupStatus.PAUSED, reason="low_balance_unpaid"
    )

    async with clean_db.acquire() as conn:
        left = await conn.fetchrow(
            "SELECT status, moderation_enabled FROM groups WHERE group_id = $1",
            group_id,
        )
        # The dangerous post-leave state: the row reads like a live group, but
        # moderation is off and the mapping that a payment would join on is gone.
        assert left["status"] == "paused"
        assert not left["moderation_enabled"]
        assert (
            await conn.fetchrow(
                "SELECT 1 FROM group_administrators WHERE group_id = $1", group_id
            )
        ) is None

    # 3. The customer tops up and re-adds the bot.
    await update_group_admins(group_id, [admin_id], ["admin"], "Back", None)

    async with clean_db.acquire() as conn:
        after = await conn.fetchrow(
            "SELECT status, moderation_enabled FROM groups WHERE group_id = $1",
            group_id,
        )

    assert after["status"] == "active"
    assert after["moderation_enabled"], (
        "re-add left moderation_enabled=false: every message is dropped at "
        "validation and the group is silently unmoderated (issue #41)"
    )


@pytest.mark.asyncio
async def test_update_group_admins_fresh_insert_no_reactivation_event(
    patched_db_conn, clean_db
):
    """Brand-new group: upsert inserts, status defaults to active, no reactivation event."""
    group_id = -1004
    admin_id = 9004
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO administrators (admin_id) VALUES ($1)", admin_id
        )

    await update_group_admins(group_id, [admin_id], ["admin"])

    async with clean_db.acquire() as conn:
        ev = await conn.fetchrow(
            "SELECT 1 FROM entity_events WHERE entity_id = $1", group_id
        )
        assert ev is None  # no event for fresh insert


@pytest.mark.asyncio
async def test_update_group_admins_already_active_no_reactivation_event(
    patched_db_conn, clean_db
):
    """Re-add on already-active group: no reactivation event (no transition)."""
    group_id = -1005
    admin_id = 9005
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO groups (group_id) VALUES ($1)", group_id)
        await conn.execute(
            "INSERT INTO administrators (admin_id) VALUES ($1)", admin_id
        )

    await update_group_admins(group_id, [admin_id], ["admin"], "Already on")

    async with clean_db.acquire() as conn:
        ev = await conn.fetchrow(
            "SELECT 1 FROM entity_events WHERE entity_id = $1", group_id
        )
        assert ev is None  # status was 'active' before, no transition


# ---------- no-rights job filter ----------


@pytest.mark.asyncio
async def test_get_groups_with_no_rights_excludes_paused_and_left(
    patched_db_conn, clean_db
):
    """The daily no-rights job must skip paused/left rows (E+C)."""
    async with clean_db.acquire() as conn:
        # Active + past grace → returned
        await conn.execute(
            "INSERT INTO groups (group_id, status, no_rights_detected_at) "
            "VALUES (1, 'active', datetime('now', '-10 days'))"
        )
        # Paused + past grace → excluded
        await conn.execute(
            "INSERT INTO groups (group_id, status, no_rights_detected_at) "
            "VALUES (2, 'paused', datetime('now', '-10 days'))"
        )
        # Left + past grace → excluded
        await conn.execute(
            "INSERT INTO groups (group_id, status, no_rights_detected_at) "
            "VALUES (3, 'left', datetime('now', '-10 days'))"
        )

    result = await get_groups_with_no_rights_past_grace(7)
    assert result == [1]


# ---------- TTL 7-day + batch event ----------


@pytest.mark.asyncio
async def test_cleanup_pending_spam_examples_keeps_recent(patched_db_conn, clean_db):
    """A pending example younger than 7 days is NOT deleted (was deleted at 3d)."""
    admin_id = 8001
    text = "keep me"

    # Insert a recent pending example (use the public function, no admin_id set yet)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO administrators (admin_id) VALUES ($1)", admin_id
        )
    await insert_pending_spam_example(
        chat_id=-1001, message_id=1, text=text, effective_user_id=42
    )
    # Force the row's created_at to 3 days ago (the OLD boundary)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE spam_examples SET created_at = datetime('now', '-3 days') "
            "WHERE message_id = 1"
        )

    deleted = await cleanup_pending_spam_examples()
    assert deleted == 0  # kept: <7 days old

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM spam_examples WHERE message_id = 1")
        assert row is not None


@pytest.mark.asyncio
async def test_cleanup_pending_spam_examples_deletes_old_writes_batch_event(
    patched_db_conn, clean_db
):
    """Pending examples older than 7 days are deleted; ONE batch event row written."""
    async with clean_db.acquire() as conn:
        await conn.execute("INSERT INTO administrators (admin_id) VALUES (7000)")
    # Three old pending examples
    for i in range(3):
        await insert_pending_spam_example(
            chat_id=-1001,
            message_id=i + 1,
            text=f"old {i}",
            effective_user_id=42,
        )
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE spam_examples SET created_at = datetime('now', '-10 days')"
        )

    deleted = await cleanup_pending_spam_examples()
    assert deleted == 3

    async with clean_db.acquire() as conn:
        # No rows left
        n = await conn.fetchval("SELECT COUNT(*) FROM spam_examples")
        assert n == 0
        # Exactly ONE batch event
        evs = await conn.fetch(
            "SELECT action, reason, old_row FROM entity_events "
            "WHERE entity_type = 'spam_example'"
        )
        assert len(evs) == 1
        ev = evs[0]
        assert ev["action"] == "ttl_pending_cleanup"
        assert ev["reason"] == "ttl_7d"
        assert json.loads(ev["old_row"])["deleted_count"] == 3


@pytest.mark.asyncio
async def test_scheduled_tasks_pending_spam_default_is_seven(monkeypatch):
    """_get_cache_ttl_days defaults pending_spam to 7 when config omits it."""
    monkeypatch.setattr(scheduled_tasks, "load_config", lambda: {"cache": {}})
    ttls = scheduled_tasks._get_cache_ttl_days()
    assert ttls["pending_spam"] == 7


@pytest.mark.asyncio
async def test_scheduled_tasks_pending_spam_reads_config(monkeypatch):
    """_get_cache_ttl_days honors config.cache.pending_spam_ttl_days."""
    monkeypatch.setattr(
        scheduled_tasks,
        "load_config",
        lambda: {"cache": {"pending_spam_ttl_days": 14}},
    )
    ttls = scheduled_tasks._get_cache_ttl_days()
    assert ttls["pending_spam"] == 14


@pytest.mark.asyncio
async def test_cleanup_group_data_missing_group_writes_no_phantom_event(
    patched_db_conn, clean_db
):
    """cleanup_group_data on a non-existent group_id must not write a phantom
    entity_events row (audit integrity) and must not raise."""
    missing_id = -99999
    await cleanup_group_data(
        missing_id, status=GroupStatus.LEFT, reason="inaccessible_chat"
    )

    async with clean_db.acquire() as conn:
        evs = await conn.fetch(
            "SELECT * FROM entity_events WHERE entity_id = $1", missing_id
        )
        assert evs == []
        # No mappings were deleted (nothing to delete), group still absent
        row = await conn.fetchrow(
            "SELECT 1 FROM groups WHERE group_id = $1", missing_id
        )
        assert row is None


@pytest.mark.asyncio
async def test_get_group_reads_status_from_db(patched_db_conn, clean_db):
    """get_group must surface the stored lifecycle status (review finding #1)."""
    from app.database.group_operations import get_group

    group_id = -1006
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, status) VALUES ($1, 'Paused', 'paused')",
            group_id,
        )
    grp = await get_group(group_id)
    assert grp is not None
    assert grp.status == GroupStatus.PAUSED
