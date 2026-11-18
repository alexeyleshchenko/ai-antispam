"""#156: the destructive cleanup must not be reachable by default.

Measured incident (2026-11-16, live): group `-1002496127723` was paused ON PURPOSE
by the no-rights job (`group_paused / no_rights_no_reachable_admin` at
21:01:07.640980Z) and then hard-wiped **0.766804 s later**
(`group_left / notification_delivery_failure` at 21:01:08.407784Z). The pause
exists to preserve the roster; the wipe destroyed it, and `cleanup_group_data`
kept no pre-image of the two mapping tables, so the rows were unrecoverable.

Two guarantees, one test each:
  1. A delivery failure on a `paused` row performs NO delete of
     `group_administrators` or `approved_members` — with an in-test positive
     control proving the same machinery DOES delete when a caller asserts the
     authority explicitly (HQ #151: the ACTOR is the predicate, status is
     corroboration only — so the flag stays, and the DEFAULT is the protection).
  2. When a wipe does happen, the mappings are reconstructable from the
     `group_mappings_deleted` tombstone written in the same transaction.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.notifications import notify_admins_with_fallback_and_cleanup
from app.database.group_operations import cleanup_group_data


async def _seed_paused_group_with_mappings(clean_db, group_id, admin_id, member_id):
    """A paused group that still owns one admin mapping and one approved member."""
    async with clean_db.acquire() as conn:
        # `group_administrators.admin_id` is FK-constrained to `administrators`.
        await conn.execute(
            "INSERT INTO administrators (admin_id) VALUES ($1)", admin_id
        )
        await conn.execute(
            "INSERT INTO groups (group_id, status) VALUES ($1, $2)",
            group_id,
            "paused",
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            group_id,
            admin_id,
        )
        await conn.execute(
            "INSERT INTO approved_members (group_id, member_id) VALUES ($1, $2)",
            group_id,
            member_id,
        )


async def _mapping_counts(clean_db, group_id):
    """(admin rows, approved rows) — read straight from the tables."""
    async with clean_db.acquire() as conn:
        admins = await conn.fetchval(
            "SELECT count(*) FROM group_administrators WHERE group_id = $1", group_id
        )
        approved = await conn.fetchval(
            "SELECT count(*) FROM approved_members WHERE group_id = $1", group_id
        )
    return admins, approved


def _unreachable_bot():
    """Every notification leg fails; only `leave_chat` would succeed."""
    mock_bot = MagicMock()
    mock_bot.get_chat = AsyncMock(side_effect=Exception("Cannot reach admin"))
    mock_bot.send_message = AsyncMock(side_effect=Exception("Cannot send message"))
    mock_bot.leave_chat = AsyncMock()
    return mock_bot


@pytest.mark.asyncio
async def test_paused_group_not_cleaned(patched_db_conn, clean_db):
    """A delivery failure on a `paused` row must not touch its mappings (#156).

    Called with NO `cleanup_if_group_fails` argument on purpose: the default is
    the protection. The positive control below asserts that the same call with
    the flag set DOES delete, so this test can fail — an assertion that no test
    can contradict certifies nothing.
    """
    group_id, admin_id, member_id = -1007801, 7801, 78011
    await _seed_paused_group_with_mappings(clean_db, group_id, admin_id, member_id)
    assert await _mapping_counts(clean_db, group_id) == (1, 1)

    # `perform_complete_group_cleanup` reaches the module-level bot; patch it so a
    # control run cannot talk to Telegram.
    with patch("app.common.notifications.bot", new=_unreachable_bot()):
        result = await notify_admins_with_fallback_and_cleanup(
            _unreachable_bot(),
            [admin_id],
            group_id,
            "Test message",
        )

        # The failure was real (all legs failed) and no cleanup was claimed.
        assert result["notified_private"] == []
        assert result["group_notified"] is False
        assert result["group_cleaned_up"] is False

        # THE ASSERTION: both mapping sets survive a delivery failure on a paused row.
        assert await _mapping_counts(clean_db, group_id) == (1, 1)

        async with clean_db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status FROM groups WHERE group_id = $1", group_id
            )
        assert row["status"] == "paused"

        # POSITIVE CONTROL: the flag is still a real gate. A caller that asserts
        # the authority (HQ #151: a human removal cleans up even when paused) gets
        # the wipe, so the assertion above is capable of failing.
        await notify_admins_with_fallback_and_cleanup(
            _unreachable_bot(),
            [admin_id],
            group_id,
            "Test message",
            cleanup_if_group_fails=True,
        )
        assert await _mapping_counts(clean_db, group_id) == (0, 0)


@pytest.mark.asyncio
async def test_cleanup_group_data_writes_mappings_tombstone(patched_db_conn, clean_db):
    """The wipe must leave a reconstructable record of what it destroyed (#156)."""
    group_id, admin_id, member_id = -1007802, 7802, 78021
    await _seed_paused_group_with_mappings(clean_db, group_id, admin_id, member_id)

    await cleanup_group_data(group_id, status="left", reason="test_wipe")

    # The rows are gone — this is the destructive path, by design.
    assert await _mapping_counts(clean_db, group_id) == (0, 0)

    async with clean_db.acquire() as conn:
        event = await conn.fetchrow(
            "SELECT action, reason, old_row FROM entity_events "
            "WHERE entity_type = 'group' AND entity_id = $1 "
            "AND action = 'group_mappings_deleted'",
            group_id,
        )

    assert event is not None, "no group_mappings_deleted tombstone was written"
    assert event["reason"] == "test_wipe"

    payload = event["old_row"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    # Both mapping sets are reconstructable from the tombstone alone.
    assert [row["admin_id"] for row in payload["group_administrators"]] == [admin_id]
    assert [row["member_id"] for row in payload["approved_members"]] == [member_id]
