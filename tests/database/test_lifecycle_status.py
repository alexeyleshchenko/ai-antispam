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

    await cleanup_group_data(group_id, status=GroupStatus.PAUSED, reason="low_balance_unpaid")

    async with clean_db.acquire() as conn:
        # Row survives with new status
        row = await conn.fetchrow("SELECT status FROM groups WHERE group_id = $1", group_id)
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

    await cleanup_group_data(group_id, status=GroupStatus.LEFT, reason="inaccessible_chat")

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
    patched_db_conn, clean_db, monkeypatch
):
    """Re-add flips paused/left back to active and clears no_rights_detected_at.

    The SQLite test adapter rewrites the groups upsert (`ON CONFLICT DO UPDATE`
    without RETURNING) as INSERT OR REPLACE, which would reset `status` to the
    default `active` and defeat the `prior` check — a harness limitation, not
    production behavior (Postgres keeps the existing row). So the upsert is
    neutralized with INSERT OR IGNORE here, and the reactivation UPDATE itself
    is verified via a SQL spy on the statements actually issued.
    """
    from app.database import group_operations

    group_id = -1003
    admin_id = 9003

    orig_get_pool = group_operations.get_pool

    class _SpyPool:
        def __init__(self, inner):
            self._inner = inner
            self.statements = []

        def acquire(self):
            return _SpyConn(self._inner.acquire(), self.statements)

    class _SpyConn:
        def __init__(self, inner, statements):
            self._inner = inner
            self.statements = statements

        async def execute(self, query, *args):
            self.statements.append(query)
            # Neutralize ONLY the adapter-unfaithful upsert (see docstring).
            if "ON CONFLICT" in query and "INSERT INTO groups" in query:
                return await self._inner.execute(
                    "INSERT OR IGNORE INTO groups (group_id) VALUES (?)", args[0]
                )
            return await self._inner.execute(query, *args)

        def __getattr__(self, name):
            return getattr(self._inner, name)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return await self._inner.__aexit__(*exc)

    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, status, no_rights_detected_at) "
            "VALUES ($1, 'paused', CURRENT_TIMESTAMP)",
            group_id,
        )
        await conn.execute(
            "INSERT INTO administrators (admin_id) VALUES ($1)", admin_id
        )

    spy = _SpyPool(await orig_get_pool())

    async def _get_pool():
        return spy

    monkeypatch.setattr(group_operations, "get_pool", _get_pool)

    await update_group_admins(group_id, [admin_id], ["admin"], "Reactivated", "@r")

    # Row is active, no_rights_detected_at cleared
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, no_rights_detected_at FROM groups WHERE group_id = $1",
            group_id,
        )
        assert row["status"] == "active"
        assert row["no_rights_detected_at"] is None
        # Reactivation event written
        ev = await conn.fetchrow(
            "SELECT action, reason FROM entity_events "
            "WHERE entity_type = 'group' AND entity_id = $1",
            group_id,
        )
        assert ev is not None
        assert ev["action"] == "group_reactivated"

    # SQL spy: the reactivation UPDATE was actually issued (not skipped)
    assert any(
        "SET status" in s and "no_rights_detected_at = NULL" in s
        for s in spy.statements
    ), f"reactivation UPDATE missing; got:\n{spy.statements}"


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
        await conn.execute(
            "INSERT INTO groups (group_id) VALUES ($1)", group_id
        )
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
        row = await conn.fetchrow(
            "SELECT 1 FROM spam_examples WHERE message_id = 1"
        )
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
            chat_id=-1001, message_id=i + 1, text=f"old {i}", effective_user_id=42,
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
        scheduled_tasks, "load_config",
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
    await cleanup_group_data(missing_id, status=GroupStatus.LEFT, reason="inaccessible_chat")

    async with clean_db.acquire() as conn:
        evs = await conn.fetch(
            "SELECT * FROM entity_events WHERE entity_id = $1", missing_id
        )
        assert evs == []
        # No mappings were deleted (nothing to delete), group still absent
        row = await conn.fetchrow("SELECT 1 FROM groups WHERE group_id = $1", missing_id)
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
