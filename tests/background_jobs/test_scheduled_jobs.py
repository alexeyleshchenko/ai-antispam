"""Unit tests for the unified scheduled jobs runner (metadata heal wiring)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.background_jobs.scheduled_tasks import run_scheduled_jobs


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
        "new": 4,
        "demoted": 0,
        "total": 3,
    }, summary


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
