"""Tests for the silent-unmoderation monitor (issue #41).

A group can sit at `status=active` with `moderation_enabled=false` while a
paying admin is attached: the bot is in the chat, its rights are intact, the
balance is topped up — and `validation` drops every message at the gate, so
nothing is caught and no credit is ever deducted. No surface reported that
state, so a paying customer's groups stayed silent for ~36 days.

These tests pin the predicate, including the two cases that must stay QUIET,
because a monitor that fires on the by-design cases gets ignored.
"""

import pytest

from app.background_jobs.moderation_monitor import check_unmoderated_paid_groups
from app.database.group_operations import get_paid_groups_with_moderation_off


async def _seed(conn, group_id, admin_id, *, status, moderation_enabled, credits):
    await conn.execute(
        "INSERT INTO administrators (admin_id, credits) VALUES ($1, $2)",
        admin_id,
        credits,
    )
    await conn.execute(
        "INSERT INTO groups (group_id, title, status, moderation_enabled) "
        "VALUES ($1, $2, $3, $4)",
        group_id,
        f"Group {group_id}",
        status,
        moderation_enabled,
    )
    await conn.execute(
        "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
        group_id,
        admin_id,
    )


@pytest.mark.asyncio
async def test_reports_active_group_with_paying_admin_and_moderation_off(
    patched_db_conn, clean_db
):
    """The issue #41 state: active, paying admin attached, moderation off."""
    async with clean_db.acquire() as conn:
        await _seed(
            conn,
            -2001,
            8001,
            status="active",
            moderation_enabled=0,
            credits=500,
        )

    reported = await check_unmoderated_paid_groups()

    assert len(reported) == 1
    assert reported[0]["group_id"] == -2001
    assert reported[0]["paying_admin_count"] == 1
    assert reported[0]["max_credits"] == 500
    # The query itself, not only the wrapper, carries the predicate.
    assert [g["group_id"] for g in await get_paid_groups_with_moderation_off()] == [
        -2001
    ]


@pytest.mark.asyncio
async def test_quiet_when_moderation_is_on(patched_db_conn, clean_db):
    """Healthy group: moderation on, paying admin — must not be reported."""
    async with clean_db.acquire() as conn:
        await _seed(
            conn,
            -2002,
            8002,
            status="active",
            moderation_enabled=1,
            credits=500,
        )

    assert await check_unmoderated_paid_groups() == []


@pytest.mark.asyncio
async def test_quiet_when_admins_are_not_paying(patched_db_conn, clean_db):
    """Zero-credit admins: unmoderated BY DESIGN (low-balance pause), not a defect."""
    async with clean_db.acquire() as conn:
        await _seed(
            conn,
            -2003,
            8003,
            status="active",
            moderation_enabled=0,
            credits=0,
        )

    assert await check_unmoderated_paid_groups() == []


@pytest.mark.asyncio
async def test_quiet_when_group_is_paused(patched_db_conn, clean_db):
    """Paused group: the bot is not in the chat, so nothing is being dropped."""
    async with clean_db.acquire() as conn:
        await _seed(
            conn,
            -2004,
            8004,
            status="paused",
            moderation_enabled=0,
            credits=500,
        )

    assert await check_unmoderated_paid_groups() == []


@pytest.mark.asyncio
async def test_counts_only_paying_admins_in_a_mixed_group(patched_db_conn, clean_db):
    """Mixed group: one paying admin, one broke — counted once, with the payer's balance."""
    async with clean_db.acquire() as conn:
        await _seed(
            conn,
            -2005,
            8005,
            status="active",
            moderation_enabled=0,
            credits=0,
        )
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits) VALUES ($1, $2)", 8006, 250
        )
        await conn.execute(
            "INSERT INTO group_administrators (group_id, admin_id) VALUES ($1, $2)",
            -2005,
            8006,
        )

    reported = await check_unmoderated_paid_groups()

    assert len(reported) == 1
    assert reported[0]["paying_admin_count"] == 1
    assert reported[0]["max_credits"] == 250
