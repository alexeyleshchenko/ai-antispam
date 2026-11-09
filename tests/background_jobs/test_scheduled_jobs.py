"""Unit tests for the unified scheduled jobs runner (metadata heal wiring)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
)

from app.background_jobs.scheduled_tasks import run_scheduled_jobs
from app.database.group_operations import BotRights


@pytest.fixture(autouse=True)
def _fully_privileged_bot():
    """Default the #143 bot-rights probe to "the bot holds both rights".

    Every sweep test below predates #143 and patches only the roster read, so
    without this they would each reach api.telegram.org once per seeded group —
    a real network call from a unit test, and a summary that depends on it.
    The #143 tests override this inside their own body, which wins because the
    fixture has already applied its patch.
    """
    full = BotRights("administrator", True, True)
    with patch(
        "app.database.group_operations.probe_bot_moderation_rights",
        new_callable=AsyncMock,
        return_value=full,
    ):
        yield


@pytest.mark.asyncio
async def test_run_scheduled_jobs_calls_heal_bare_group_rows():
    """The daily job runner invokes the bare-row metadata heal."""
    with (
        patch(
            "app.background_jobs.scheduled_tasks.run_low_balance_checks",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.leave_no_rights_groups",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.cleanup_old_lookup_entries",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.cleanup_old_message_history",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.cleanup_pending_spam_examples",
            new_callable=AsyncMock,
        ),
        patch(
            "app.background_jobs.scheduled_tasks.heal_bare_group_rows",
            new_callable=AsyncMock,
        ) as mock_heal,
        patch(
            "app.background_jobs.scheduled_tasks.sweep_group_administrators",
            new_callable=AsyncMock,
        ) as mock_sweep,
    ):
        await run_scheduled_jobs()

    mock_heal.assert_awaited_once()
    # #130 writer 3: the roster sweep is part of the daily bundle.
    mock_sweep.assert_awaited_once()


# --- #130: the daily roster sweep -------------------------------------------


@pytest.mark.asyncio
async def test_sweep_counts_a_failing_group_and_keeps_sweeping(
    patched_db_conn, clean_db
):
    """One group the bot was kicked from must not cost the rest their refresh."""
    from app.database.group_operations import (
        SyncReport,
        sweep_group_administrators,
    )

    good = (-1001400001, -1001400002)
    bad = -1001400003
    async with clean_db.acquire() as conn:
        for group_id in (*good, bad):
            await conn.execute(
                "INSERT INTO groups (group_id, title, status) VALUES ($1, 'g', 'active')",
                group_id,
            )

    async def fake_sync(chat_id, *, apply):
        if chat_id == bad:
            raise RuntimeError("kicked from the group")
        return SyncReport(
            chat_id=chat_id,
            title="g",
            telegram=2,
            known=0,
            new=2,
            demoted=0,
            applied=apply,
        )

    with patch(
        "app.database.group_operations.sync_group_administrators",
        side_effect=fake_sync,
    ):
        summary = await sweep_group_administrators()

    assert summary == {
        "synced": 2,
        "failed": 1,
        "retired": 0,
        "migrated": 0,
        "no_rights": 0,
        "dormant": 0,
        "new": 4,
        "demoted": 0,
        "rights_flagged": 0,
        "rights_cleared": 0,
        "rights_unverified": 0,
        "total": 3,
        # #144: the detection payload for a degraded administrator. Empty here
        # because no probe in this test reports one.
        "rights_degraded": [],
    }, summary

    # D4 positive control: an error no predicate claims stays LOUD and must not
    # be "handled" by touching the row. This is the whole point of the
    # classifier — a real defect must not be laundered into a lifecycle event.
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, no_rights_detected_at FROM groups WHERE group_id = $1",
            bad,
        )
        events = await conn.fetch(
            "SELECT id FROM entity_events WHERE entity_id = $1", bad
        )
    assert row["status"] == "active"
    assert row["no_rights_detected_at"] is None
    assert events == []


@pytest.mark.asyncio
async def test_sweep_only_reads_active_groups(patched_db_conn, clean_db):
    """A paused group's roster is not worth a Telegram call."""
    from app.database.group_operations import (
        SyncReport,
        sweep_group_administrators,
    )

    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, status) VALUES "
            "(-1001400011, 'active one', 'active')"
        )
        await conn.execute(
            "INSERT INTO groups (group_id, title, status) VALUES "
            "(-1001400012, 'paused one', 'inactive')"
        )

    seen: list[int] = []

    async def fake_sync(chat_id, *, apply):
        seen.append(chat_id)
        return SyncReport(
            chat_id=chat_id,
            title="g",
            telegram=1,
            known=1,
            new=0,
            demoted=0,
            applied=apply,
        )

    with patch(
        "app.database.group_operations.sync_group_administrators",
        side_effect=fake_sync,
    ):
        summary = await sweep_group_administrators()

    assert seen == [-1001400011], "the inactive group must not be swept"
    assert summary["total"] == 1


@pytest.mark.asyncio
async def test_sweep_respects_the_limit(patched_db_conn, clean_db):
    from app.database.group_operations import (
        SyncReport,
        sweep_group_administrators,
    )

    async with clean_db.acquire() as conn:
        for offset in range(4):
            await conn.execute(
                "INSERT INTO groups (group_id, title, status) VALUES ($1, 'g', 'active')",
                -1001400100 - offset,
            )

    calls: list[int] = []

    async def fake_sync(chat_id, *, apply):
        calls.append(chat_id)
        return SyncReport(
            chat_id=chat_id,
            title="g",
            telegram=0,
            known=0,
            new=0,
            demoted=0,
            applied=apply,
        )

    with patch(
        "app.database.group_operations.sync_group_administrators",
        side_effect=fake_sync,
    ):
        summary = await sweep_group_administrators(limit=2)

    assert len(calls) == 2
    assert summary["total"] == 2


# --- #130: the sweep classifies a failure instead of only counting it --------

GONE = TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
NO_RIGHTS = TelegramBadRequest(
    method=MagicMock(), message="Bad Request: member list is inaccessible"
)
KICKED = TelegramForbiddenError(
    method=MagicMock(), message="Forbidden: bot was kicked from the group chat"
)
MOVED = TelegramMigrateToChat(
    method=MagicMock(),
    message="Bad Request: group chat was upgraded to a supergroup chat",
    migrate_to_chat_id=-1003721264646,
)


async def _seed_group(clean_db, group_id, *, last_active="2026-11-01 00:00:00"):
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, status, moderation_enabled, "
            "last_active) VALUES ($1, 'g', 'active', 1, $2)",
            group_id,
            last_active,
        )


async def _seed_seat(clean_db, group_id, admin_id, *, registered=False, credits=0):
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits) VALUES ($1, $2)",
            admin_id,
            credits,
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id, is_registered) "
            "VALUES ($1, $2, $3)",
            group_id,
            admin_id,
            registered,
        )


@pytest.mark.asyncio
async def test_sweep_retires_a_chat_not_found_group(patched_db_conn, clean_db):
    """A chat Telegram says is gone is RETIRED — status only, roster intact (D1)."""
    from app.database.group_operations import (
        sweep_group_administrators,
    )

    group_id, admin_id = -1001400021, 1400021
    await _seed_group(clean_db, group_id)
    await _seed_seat(clean_db, group_id, admin_id)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO approved_members (group_id, member_id) VALUES ($1, $2)",
            group_id,
            admin_id,
        )

    async def fake_sync(chat_id, *, apply):
        raise GONE

    with patch(
        "app.database.group_operations.sync_group_administrators",
        side_effect=fake_sync,
    ):
        summary = await sweep_group_administrators()

    assert summary["retired"] == 1
    assert summary["failed"] == 0

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, bot_removed_at, migrated_to_group_id FROM groups "
            "WHERE group_id = $1",
            group_id,
        )
        events = await conn.fetch(
            "SELECT action, reason FROM entity_events WHERE entity_id = $1", group_id
        )
        seats = await conn.fetch(
            "SELECT admin_id FROM group_administrators WHERE group_id = $1", group_id
        )
        members = await conn.fetch(
            "SELECT member_id FROM approved_members WHERE group_id = $1", group_id
        )

    assert row["status"] == "left"
    assert row["bot_removed_at"] is not None
    assert row["migrated_to_group_id"] is None
    assert len(events) == 1
    assert events[0]["action"] == "group_retired"
    assert events[0]["reason"] == "sweep_inaccessible"
    # D1: retirement is a STATUS, not a purge — billing links survive
    assert [s["admin_id"] for s in seats] == [admin_id]
    assert [m["member_id"] for m in members] == [admin_id]


@pytest.mark.asyncio
async def test_sweep_migrates_an_upgraded_group(patched_db_conn, clean_db):
    """A Telegram upgrade is MIGRATED, not retired: the new id inherits the identity."""
    from app.database.group_operations import (
        sweep_group_administrators,
    )

    old_id, new_id, admin_id = -1001400031, MOVED.migrate_to_chat_id, 1400031
    await _seed_group(clean_db, old_id)
    await _seed_seat(clean_db, old_id, admin_id, registered=True)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE groups SET bot_added_at = '2026-10-01 12:00:00' "
            "WHERE group_id = $1",
            old_id,
        )
        await conn.execute(
            "INSERT INTO approved_members (group_id, member_id) VALUES ($1, $2)",
            old_id,
            admin_id,
        )

    async def fake_sync(chat_id, *, apply):
        raise MOVED

    with patch(
        "app.database.group_operations.sync_group_administrators",
        side_effect=fake_sync,
    ):
        summary = await sweep_group_administrators()

    assert summary["migrated"] == 1
    assert summary["retired"] == 0

    async with clean_db.acquire() as conn:
        old_row = await conn.fetchrow(
            "SELECT status, migrated_to_group_id FROM groups WHERE group_id = $1",
            old_id,
        )
        new_row = await conn.fetchrow(
            "SELECT bot_added_at, moderation_enabled FROM groups WHERE group_id = $1",
            new_id,
        )
        new_seats = await conn.fetch(
            "SELECT admin_id, is_registered FROM group_administrators "
            "WHERE group_id = $1",
            new_id,
        )
        old_seats = await conn.fetch(
            "SELECT admin_id FROM group_administrators WHERE group_id = $1", old_id
        )

    assert old_row["status"] == "left"
    assert old_row["migrated_to_group_id"] == new_id
    assert new_row is not None
    # Q2: verbatim, not NOW() — a fresh 72 h would re-grant trust on unchanged
    # evidence, and NULL would kill the pre-existing window permanently.
    assert str(new_row["bot_added_at"]).startswith("2026-10-01 12:00:00")
    assert bool(new_row["moderation_enabled"]) is True
    assert [s["admin_id"] for s in new_seats] == [admin_id]
    assert bool(new_seats[0]["is_registered"]) is True
    # The old roster is not stolen, only copied
    assert [s["admin_id"] for s in old_seats] == [admin_id]


@pytest.mark.asyncio
async def test_sweep_flags_member_list_inaccessible_without_retiring(
    patched_db_conn, clean_db
):
    """D2: a rights error is NOT absence — the grace flow decides, not the sweep."""
    from app.database.group_operations import sweep_group_administrators

    group_id = -1001400041
    await _seed_group(clean_db, group_id)

    async def fake_sync(chat_id, *, apply):
        raise NO_RIGHTS

    with patch(
        "app.database.group_operations.sync_group_administrators",
        side_effect=fake_sync,
    ):
        summary = await sweep_group_administrators()

    assert summary["no_rights"] == 1
    assert summary["retired"] == 0

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, no_rights_detected_at FROM groups WHERE group_id = $1",
            group_id,
        )
        events = await conn.fetch(
            "SELECT id FROM entity_events WHERE entity_id = $1", group_id
        )

    assert row["no_rights_detected_at"] is not None
    assert row["status"] == "active"
    assert events == []


@pytest.mark.asyncio
async def test_sweep_retires_a_kicked_group(patched_db_conn, clean_db):
    """The ForbiddenError leg reaches the same predicate by a different class."""
    from app.database.group_operations import sweep_group_administrators

    group_id = -1001400051
    await _seed_group(clean_db, group_id)

    async def fake_sync(chat_id, *, apply):
        raise KICKED

    with patch(
        "app.database.group_operations.sync_group_administrators",
        side_effect=fake_sync,
    ):
        summary = await sweep_group_administrators()

    assert summary["retired"] == 1

    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM groups WHERE group_id = $1", group_id
        )
    assert row["status"] == "left"


@pytest.mark.asyncio
async def test_sweep_second_run_is_quiet(patched_db_conn, clean_db):
    """Retirement takes the row out of the active set — the next run does not repeat it."""
    from app.database.group_operations import sweep_group_administrators

    group_id = -1001400061
    await _seed_group(clean_db, group_id)

    async def fake_sync(chat_id, *, apply):
        raise GONE

    with patch(
        "app.database.group_operations.sync_group_administrators",
        side_effect=fake_sync,
    ):
        first = await sweep_group_administrators()
        second = await sweep_group_administrators()

    assert first["retired"] == 1
    assert second["total"] == 0
    assert second["retired"] == 0
    assert second["failed"] == 0

    async with clean_db.acquire() as conn:
        events = await conn.fetch(
            "SELECT id FROM entity_events WHERE entity_id = $1", group_id
        )
    assert len(events) == 1


@pytest.mark.asyncio
async def test_sweep_counts_dormant_only_for_synced_groups(patched_db_conn, clean_db):
    """Dormant = quiet AND unpaid, and only for groups the sweep actually synced."""
    from app.database.group_operations import (
        SyncReport,
        sweep_group_administrators,
    )

    quiet_unpaid, quiet_paid, failing = -1001400071, -1001400072, -1001400073
    old = "2026-01-01 00:00:00"  # >90 days before any plausible run date
    await _seed_group(clean_db, quiet_unpaid, last_active=old)
    await _seed_group(clean_db, quiet_paid, last_active=old)
    await _seed_seat(clean_db, quiet_paid, 1400072, registered=True, credits=50)
    await _seed_group(clean_db, failing, last_active=old)

    async def fake_sync(chat_id, *, apply):
        if chat_id == failing:
            raise RuntimeError("kaboom")
        return SyncReport(
            chat_id=chat_id,
            title="g",
            telegram=1,
            known=1,
            new=0,
            demoted=0,
            applied=apply,
        )

    with patch(
        "app.database.group_operations.sync_group_administrators",
        side_effect=fake_sync,
    ):
        summary = await sweep_group_administrators()

    # The paid quiet group is NOT dormant: it is a live customer, not dead weight
    assert summary["dormant"] == 1
    # The failing group is counted as failed, never folded into a lifecycle bucket
    assert summary["retired"] == 0
    assert summary["migrated"] == 0
    assert summary["no_rights"] == 0
    assert summary["failed"] == 1


# --- #143: the sweep asserts the bot's own membership -----------------------


def _bot_admin(*, can_delete: bool, can_restrict: bool) -> BotRights:
    return BotRights(
        status="administrator",
        can_delete_messages=can_delete,
        can_restrict_members=can_restrict,
    )


@pytest.mark.asyncio
async def test_sweep_flags_a_group_the_bot_cannot_moderate(patched_db_conn, clean_db):
    """A readable roster says nothing about whether we can delete anything in it."""
    from app.database.group_operations import (
        SyncReport,
        sweep_group_administrators,
    )

    group_id = -1001430001
    await _seed_group(clean_db, group_id)

    async def fake_sync(chat_id, *, apply):
        return SyncReport(
            chat_id=chat_id,
            title="g",
            telegram=1,
            known=1,
            new=0,
            demoted=0,
            applied=apply,
        )

    with (
        patch(
            "app.database.group_operations.sync_group_administrators",
            side_effect=fake_sync,
        ),
        patch(
            "app.database.group_operations.probe_bot_moderation_rights",
            new_callable=AsyncMock,
            return_value=_bot_admin(can_delete=False, can_restrict=False),
        ),
    ):
        summary = await sweep_group_administrators()

    assert summary["rights_flagged"] == 1
    assert summary["rights_unverified"] == 0
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, no_rights_detected_at FROM groups WHERE group_id = $1",
            group_id,
        )
    # Flagged, never retired: the grace job owns the pause, and status is not ours.
    assert row["status"] == "active"
    assert row["no_rights_detected_at"] is not None


@pytest.mark.asyncio
async def test_sweep_counts_an_unverified_probe_without_touching_the_row(
    patched_db_conn, clean_db
):
    """ "We could not ask" is a number in the summary, not health and not a flag."""
    from app.database.group_operations import (
        SyncReport,
        sweep_group_administrators,
    )

    group_id = -1001430002
    await _seed_group(clean_db, group_id)

    async def fake_sync(chat_id, *, apply):
        return SyncReport(
            chat_id=chat_id,
            title="g",
            telegram=1,
            known=1,
            new=0,
            demoted=0,
            applied=apply,
        )

    with (
        patch(
            "app.database.group_operations.sync_group_administrators",
            side_effect=fake_sync,
        ),
        patch(
            "app.database.group_operations.probe_bot_moderation_rights",
            new_callable=AsyncMock,
            side_effect=RuntimeError("api down"),
        ),
    ):
        summary = await sweep_group_administrators()

    assert summary["rights_unverified"] == 1
    assert summary["rights_flagged"] == 0
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT no_rights_detected_at FROM groups WHERE group_id = $1", group_id
        )
    assert row["no_rights_detected_at"] is None


@pytest.mark.asyncio
async def test_sweep_clears_the_clock_when_rights_return(patched_db_conn, clean_db):
    """The restore path: a group that regained its rights leaves the grace flow."""
    from app.database.group_operations import (
        SyncReport,
        sweep_group_administrators,
    )

    group_id = -1001430003
    await _seed_group(clean_db, group_id)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE groups SET no_rights_detected_at = '2026-11-01 00:00:00' "
            "WHERE group_id = $1",
            group_id,
        )

    async def fake_sync(chat_id, *, apply):
        return SyncReport(
            chat_id=chat_id,
            title="g",
            telegram=1,
            known=1,
            new=0,
            demoted=0,
            applied=apply,
        )

    with (
        patch(
            "app.database.group_operations.sync_group_administrators",
            side_effect=fake_sync,
        ),
        patch(
            "app.database.group_operations.probe_bot_moderation_rights",
            new_callable=AsyncMock,
            return_value=_bot_admin(can_delete=True, can_restrict=True),
        ),
    ):
        summary = await sweep_group_administrators()

    assert summary["rights_cleared"] == 1
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT no_rights_detected_at FROM groups WHERE group_id = $1", group_id
        )
    assert row["no_rights_detected_at"] is None


@pytest.mark.asyncio
async def test_rights_monitor_reports_and_repairs_nothing(patched_db_conn, clean_db):
    """The inverse report: moderated on, bot rightsless — and it must not write."""
    from app.background_jobs.moderation_monitor import (
        check_moderated_groups_without_bot_rights,
    )

    paying, unpaid, off = -1001430011, -1001430012, -1001430013
    await _seed_group(clean_db, paying)
    await _seed_seat(clean_db, paying, 1430011, registered=True, credits=96)
    await _seed_group(clean_db, unpaid)
    await _seed_group(clean_db, off)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE groups SET no_rights_detected_at = '2026-11-09 12:00:00' "
            "WHERE group_id = ANY($1)",
            [paying, unpaid, off],
        )
        # A group with moderation OFF is #41's population, not this one's.
        await conn.execute(
            "UPDATE groups SET moderation_enabled = 0 WHERE group_id = $1", off
        )

    groups = await check_moderated_groups_without_bot_rights()

    # Ordered by group_id, which sorts the two negative ids by magnitude.
    assert [group["group_id"] for group in groups] == [unpaid, paying]
    assert [group["has_paying_admin"] for group in groups] == [False, True]

    # Report-only: nothing about the rows moved, and the grace job still owns them.
    async with clean_db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT group_id, status, moderation_enabled, no_rights_detected_at "
            "FROM groups ORDER BY group_id"
        )
    assert [(r["group_id"], r["status"]) for r in rows] == [
        (off, "active"),
        (unpaid, "active"),
        (paying, "active"),
    ]
    assert all(r["no_rights_detected_at"] is not None for r in rows)
    # Same group_id order: `off` is the most negative of the three.
    assert [r["moderation_enabled"] for r in rows] == [0, 1, 1]


@pytest.mark.asyncio
async def test_rights_monitor_is_wired_into_the_daily_bundle():
    """The sweep sets the clock; this leg is the only thing that reads it out."""
    with (
        patch(
            "app.background_jobs.scheduled_tasks.check_moderated_groups_without_bot_rights",
            new_callable=AsyncMock,
        ) as mock_monitor,
        patch(
            "app.background_jobs.scheduled_tasks.sweep_group_administrators",
            new_callable=AsyncMock,
        ),
    ):
        await run_scheduled_jobs()

    mock_monitor.assert_awaited_once()


# --- #144: detection is one-shot, and the operator line is an ERROR ---------


@pytest.mark.asyncio
async def test_degraded_administrator_is_reported_once_then_never_again(
    patched_db_conn, clean_db
):
    """Close condition 3: the DM rides the `NULL → NOW()` transition, not the sweep.

    A group already flagged must produce NO new payload, or every daily sweep
    would send the same customer message again.
    """
    from app.database.group_operations import (
        SyncReport,
        sweep_group_administrators,
    )

    group_id = -1002496127723
    await _seed_group(clean_db, group_id)

    async def fake_sync(chat_id, *, apply):
        return SyncReport(
            chat_id=chat_id,
            title="блатные чупани",
            telegram=1,
            known=1,
            new=0,
            demoted=0,
            applied=apply,
        )

    with (
        patch(
            "app.database.group_operations.sync_group_administrators",
            side_effect=fake_sync,
        ),
        patch(
            "app.database.group_operations.probe_bot_moderation_rights",
            new_callable=AsyncMock,
            return_value=_bot_admin(can_delete=True, can_restrict=False),
        ),
    ):
        first = await sweep_group_administrators()
        second = await sweep_group_administrators()

    # First detection: one payload naming the missing right in UI vocabulary.
    assert len(first["rights_degraded"]) == 1
    assert first["rights_degraded"][0]["group_id"] == group_id
    assert first["rights_degraded"][0]["missing"] == "Ban users"
    assert first["rights_flagged"] == 1

    # Second sweep: still flagged, but nothing to notify about.
    assert second["rights_degraded"] == []
    assert second["rights_flagged"] == 0


@pytest.mark.asyncio
async def test_plain_member_is_not_in_the_detection_payload(patched_db_conn, clean_db):
    """The DM is for the degraded-administrator class only (HQ's narrowing).

    A plain member already gets a message at the leave; a detection DM as well
    would be a second customer message nobody asked for.
    """
    from app.database.group_operations import (
        SyncReport,
        sweep_group_administrators,
    )

    group_id = -1003078404548
    await _seed_group(clean_db, group_id)

    async def fake_sync(chat_id, *, apply):
        return SyncReport(
            chat_id=chat_id,
            title="member group",
            telegram=1,
            known=1,
            new=0,
            demoted=0,
            applied=apply,
        )

    with (
        patch(
            "app.database.group_operations.sync_group_administrators",
            side_effect=fake_sync,
        ),
        patch(
            "app.database.group_operations.probe_bot_moderation_rights",
            new_callable=AsyncMock,
            return_value=BotRights(
                status="member", can_delete_messages=False, can_restrict_members=False
            ),
        ),
    ):
        summary = await sweep_group_administrators()

    assert summary["rights_flagged"] == 1
    assert summary["rights_degraded"] == []


@pytest.mark.asyncio
async def test_rights_detection_reaches_the_operator_as_an_error(
    patched_db_conn, clean_db, caplog
):
    """Close condition 4: ERROR, because the Telegram handler sits at WARNING."""
    import logging

    from app.common.telegram_logging_handler import TelegramLogHandler
    from app.database.group_operations import reconcile_bot_rights

    # The receipt for "ERROR is enough": the handler drops anything below it.
    # Read from a REAL handler, not from the source — the level is set in the
    # constructor body (`super().__init__(level=WARNING)`), so an argument
    # default would not show it.
    handler = TelegramLogHandler(bot=MagicMock(), chat_id=1)
    assert handler.level == logging.WARNING

    group_id = -1002496127723
    await _seed_group(clean_db, group_id)

    with patch(
        "app.database.group_operations.probe_bot_moderation_rights",
        new_callable=AsyncMock,
        return_value=_bot_admin(can_delete=False, can_restrict=True),
    ):
        with caplog.at_level(logging.INFO, logger="app.database.group_operations"):
            result = await reconcile_bot_rights(group_id, clock_set=False)

    assert result.started is True
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, [r.getMessage() for r in caplog.records]
    assert "can_delete_messages" in errors[0].getMessage()
    assert str(group_id) in errors[0].getMessage()
