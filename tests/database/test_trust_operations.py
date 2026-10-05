"""Tests for the pre-existing-member trust store (issue #108).

The inference under test is negative: *no join event on record, therefore the
sender was already here when the bot arrived.* It is only sound while the
bot's observation of joins has been unbroken, so every leg here is a
fail-closed leg — a missing row, an unreadable instant, a removed bot, an
expired window or a coverage gap must all return False.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.database.group_operations import (
    add_member,
    get_moderation_event_count,
    increment_moderation_events,
    is_trusted_member,
    set_moderation_events,
)
from app.database.trust_operations import (
    beat_coverage,
    coverage_continuous_since,
    get_group_bot_state,
    get_member_trust_row,
    grant_pre_existing_trust,
    has_join_record,
    is_pre_existing_candidate,
    record_bot_added,
    record_bot_removed,
    record_join,
)

GROUP_ID = -1001234
MEMBER_ID = 555001


def _now() -> datetime:
    return datetime.now(UTC)


async def _insert_group(conn, group_id: int = GROUP_ID) -> None:
    await conn.execute("INSERT INTO groups (group_id, title) VALUES ($1, $2)", group_id, "T")


@pytest.mark.asyncio
async def test_schema_has_new_columns_and_tables(patched_db_conn, clean_db):
    """The migration adds every new column on a pre-existing DB, plus 2 tables."""
    async with clean_db.acquire() as conn:
        group_cols = {row[1] for row in await conn.fetch("PRAGMA table_info(groups)")}
        member_cols = {
            row[1] for row in await conn.fetch("PRAGMA table_info(approved_members)")
        }
        tables = {
            row[0]
            for row in await conn.fetch(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert {"bot_added_at", "bot_removed_at"} <= group_cols
    assert {"trust_source", "trusted_until"} <= member_cols
    assert {"member_joins", "coverage_state"} <= tables


@pytest.mark.asyncio
async def test_record_join_first_observation_wins(patched_db_conn, clean_db):
    """A redelivered join does not move `joined_at`."""
    assert await record_join(GROUP_ID, MEMBER_ID) is True
    assert await record_join(GROUP_ID, MEMBER_ID) is False
    assert await has_join_record(GROUP_ID, MEMBER_ID) is True


@pytest.mark.asyncio
async def test_has_join_record_absent_is_false(patched_db_conn, clean_db):
    assert await has_join_record(GROUP_ID, MEMBER_ID) is False


@pytest.mark.asyncio
async def test_record_bot_added_and_removed_roundtrip(patched_db_conn, clean_db):
    """A re-add clears the removal stamp; a removal stamps it."""
    async with clean_db.acquire() as conn:
        await _insert_group(conn)

    await record_bot_added(GROUP_ID)
    state = await get_group_bot_state(GROUP_ID)
    assert state["bot_added_at"] is not None
    assert state["bot_removed_at"] is None

    await record_bot_removed(GROUP_ID)
    state = await get_group_bot_state(GROUP_ID)
    assert state["bot_removed_at"] is not None

    await record_bot_added(GROUP_ID)
    state = await get_group_bot_state(GROUP_ID)
    assert state["bot_removed_at"] is None


@pytest.mark.asyncio
async def test_first_beat_establishes_continuity(patched_db_conn, clean_db):
    first = await beat_coverage()
    assert await coverage_continuous_since() == first

    # A beat inside the threshold does NOT move continuity.
    again = await beat_coverage()
    assert again == first


@pytest.mark.asyncio
async def test_beat_past_gap_resets_continuity(patched_db_conn, clean_db):
    """A gap is a coverage break: continuity restarts at the late beat."""
    await beat_coverage()
    # Force the stored last beat far into the past, then beat again.
    stale = _now() - timedelta(hours=2)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE coverage_state SET last_beat_at = $1 WHERE id = 1", stale
        )

    reset = await beat_coverage()
    assert reset > stale
    assert await coverage_continuous_since() == reset


@pytest.mark.asyncio
async def test_grant_records_source_and_expiry(patched_db_conn, clean_db):
    async with clean_db.acquire() as conn:
        await _insert_group(conn)

    await grant_pre_existing_trust(GROUP_ID, MEMBER_ID, window_hours=72)

    row = await get_member_trust_row(GROUP_ID, MEMBER_ID)
    assert row["trust_source"] == "pre_existing"
    assert row["trusted_until"] is not None


@pytest.mark.asyncio
async def test_is_trusted_member_honours_trusted_until(patched_db_conn, clean_db):
    """A live grant is trusted; an expired one is not, even with the count."""
    async with clean_db.acquire() as conn:
        await _insert_group(conn)

    await grant_pre_existing_trust(GROUP_ID, MEMBER_ID, window_hours=72)
    assert await is_trusted_member(GROUP_ID, MEMBER_ID) is True

    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE approved_members SET trusted_until = $1 WHERE group_id = $2 AND member_id = $3",
            _now() - timedelta(minutes=1),
            GROUP_ID,
            MEMBER_ID,
        )
    assert await is_trusted_member(GROUP_ID, MEMBER_ID) is False


@pytest.mark.asyncio
async def test_increment_clears_grant_and_resets_to_probation(
    patched_db_conn, clean_db
):
    """Real moderation evidence supersedes the inferred grant."""
    async with clean_db.acquire() as conn:
        await _insert_group(conn)

    await grant_pre_existing_trust(GROUP_ID, MEMBER_ID, window_hours=72)
    await increment_moderation_events(GROUP_ID, MEMBER_ID)

    row = await get_member_trust_row(GROUP_ID, MEMBER_ID)
    assert row["trust_source"] == "probation"
    assert row["trusted_until"] is None


@pytest.mark.asyncio
async def test_add_member_marks_probation_and_admin_marks_admin(
    patched_db_conn, clean_db
):
    async with clean_db.acquire() as conn:
        await _insert_group(conn)

    await add_member(GROUP_ID, MEMBER_ID)
    row = await get_member_trust_row(GROUP_ID, MEMBER_ID)
    assert row["trust_source"] == "probation"

    await set_moderation_events(GROUP_ID, MEMBER_ID, 3)
    row = await get_member_trust_row(GROUP_ID, MEMBER_ID)
    assert row["trust_source"] == "admin"
    assert await get_moderation_event_count(GROUP_ID, MEMBER_ID) == 3
    assert await is_trusted_member(GROUP_ID, MEMBER_ID) is True


@pytest.mark.asyncio
async def test_candidate_fails_closed_without_bot_add(patched_db_conn, clean_db):
    async with clean_db.acquire() as conn:
        await _insert_group(conn)

    await beat_coverage()
    assert await is_pre_existing_candidate(GROUP_ID, MEMBER_ID) is False


@pytest.mark.asyncio
async def test_candidate_true_inside_window_with_continuous_coverage(
    patched_db_conn, clean_db
):
    """The healthy path: coverage predates the add, sender has no join row."""
    async with clean_db.acquire() as conn:
        await _insert_group(conn)
        await beat_coverage()
        await conn.execute(
            "UPDATE coverage_state SET continuous_since = $1 WHERE id = 1",
            _now() - timedelta(days=10),
        )
        await conn.execute(
            "UPDATE groups SET bot_added_at = $1 WHERE group_id = $2",
            _now() - timedelta(hours=1),
            GROUP_ID,
        )

    assert await is_pre_existing_candidate(GROUP_ID, MEMBER_ID) is True


@pytest.mark.asyncio
async def test_candidate_fails_closed_on_each_gap(patched_db_conn, clean_db):
    """Neuter each leg in turn: a join row, a prior row, a stale window, a gap."""
    async with clean_db.acquire() as conn:
        await _insert_group(conn)
        await beat_coverage()
        await conn.execute(
            "UPDATE coverage_state SET continuous_since = $1 WHERE id = 1",
            _now() - timedelta(days=10),
        )
        await conn.execute(
            "UPDATE groups SET bot_added_at = $1 WHERE group_id = $2",
            _now() - timedelta(hours=1),
            GROUP_ID,
        )

    assert await is_pre_existing_candidate(GROUP_ID, MEMBER_ID) is True

    # Leg 4 — an observed join disproves "was already here".
    await record_join(GROUP_ID, MEMBER_ID)
    assert await is_pre_existing_candidate(GROUP_ID, MEMBER_ID) is False

    # Leg 5 — a prior moderation row is not a stranger.
    async with clean_db.acquire() as conn:
        await conn.execute(
            "DELETE FROM member_joins WHERE group_id = $1 AND member_id = $2",
            GROUP_ID,
            MEMBER_ID,
        )
    await add_member(GROUP_ID, MEMBER_ID)
    assert await is_pre_existing_candidate(GROUP_ID, MEMBER_ID) is False

    # Leg 2 — the window has closed.
    async with clean_db.acquire() as conn:
        await conn.execute(
            "DELETE FROM approved_members WHERE group_id = $1 AND member_id = $2",
            GROUP_ID,
            MEMBER_ID,
        )
        await conn.execute(
            "UPDATE groups SET bot_added_at = $1 WHERE group_id = $2",
            _now() - timedelta(hours=100),
            GROUP_ID,
        )
    assert await is_pre_existing_candidate(GROUP_ID, MEMBER_ID) is False

    # Leg 3 — coverage began AFTER the add, so the gap at the add is unknown.
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE groups SET bot_added_at = $1 WHERE group_id = $2",
            _now() - timedelta(hours=1),
            GROUP_ID,
        )
        await conn.execute(
            "UPDATE coverage_state SET continuous_since = $1 WHERE id = 1", _now()
        )
    assert await is_pre_existing_candidate(GROUP_ID, MEMBER_ID) is False
