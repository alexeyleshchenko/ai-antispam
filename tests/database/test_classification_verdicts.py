"""Tests for the classification verdict store (src/app/database/classification_verdicts.py).

Covers the properties the store exists for:
- a verdict round-trips (claim -> decide -> read);
- exactly ONE caller wins the classification claim, even when two arrive;
- exactly ONE caller wins the moderation claim, however many deliveries arrive;
- the DDL is idempotent;
- both cleanup paths are wired into the scheduled jobs.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.background_jobs import scheduled_tasks
from app.database.classification_verdicts import (
    DEFAULT_PENDING_STALE_MINUTES,
    DEFAULT_VERDICT_RETRY_ATTEMPTS,
    DEFAULT_VERDICT_TTL_DAYS,
    claim_moderation,
    claim_or_read,
    claim_pending,
    cleanup_old_verdicts,
    cleanup_stale_pending_verdicts,
    ensure_verdict_table,
    mark_failed,
    reclaim_failed,
    release_moderation_claim,
    store_result_id,
    store_verdict,
)

CHAT_ID = -100999
MESSAGE_ID = 4242

@pytest.mark.asyncio
async def test_absent_before_any_claim(patched_db_conn, clean_db):
    """Nothing is stored until something claims the message."""
    assert await claim_or_read(CHAT_ID, MESSAGE_ID) is None

@pytest.mark.asyncio
async def test_verdict_round_trip(patched_db_conn, clean_db):
    """claim -> decide -> read returns the verdict that was stored."""
    assert await claim_pending(CHAT_ID, MESSAGE_ID) is True

    pending = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert pending is not None
    assert pending["status"] == "pending"
    assert pending["moderated_at"] is None

    await store_verdict(CHAT_ID, MESSAGE_ID, True, 97, "crypto_investment_scam")

    decided = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert decided["status"] == "decided"
    assert bool(decided["is_spam"]) is True
    assert decided["confidence"] == 97
    assert decided["reason"] == "crypto_investment_scam"

@pytest.mark.asyncio
async def test_duplicate_pending_claim_loses(patched_db_conn, clean_db):
    """Two deliveries of one update must not both start the work.

    The loser observes the SAME row, unchanged - not a second one. This is the
    property the un-guarded path does not have: there, both callers proceed.
    """
    assert await claim_pending(CHAT_ID, MESSAGE_ID) is True
    assert await claim_pending(CHAT_ID, MESSAGE_ID) is False

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["status"] == "pending"
    assert row["moderated_at"] is None

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["status"] == "pending"

@pytest.mark.asyncio
async def test_duplicate_moderation_claim_loses(patched_db_conn, clean_db):
    """N deliveries produce exactly ONE moderation action."""
    await claim_pending(CHAT_ID, MESSAGE_ID)
    await store_verdict(CHAT_ID, MESSAGE_ID, True, 90, "spam")

    assert await claim_moderation(CHAT_ID, MESSAGE_ID) is True
    first = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert first["moderated_at"] is not None

    assert await claim_moderation(CHAT_ID, MESSAGE_ID) is False
    # The loser changed nothing: the moderation instant it observes is the
    # winner's, so N deliveries still produce ONE moderation action.
    second = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert second["moderated_at"] == first["moderated_at"]

@pytest.mark.asyncio
async def test_released_moderation_claim_can_be_retaken(patched_db_conn, clean_db):
    """A failed moderation action is retryable, not permanently stuck."""
    await claim_pending(CHAT_ID, MESSAGE_ID)
    assert await claim_moderation(CHAT_ID, MESSAGE_ID) is True
    assert await claim_moderation(CHAT_ID, MESSAGE_ID) is False

    await release_moderation_claim(CHAT_ID, MESSAGE_ID)
    assert await claim_moderation(CHAT_ID, MESSAGE_ID) is True

@pytest.mark.asyncio
async def test_result_id_stored(patched_db_conn, clean_db):
    """The moderation result id is readable back."""
    await claim_pending(CHAT_ID, MESSAGE_ID)
    await claim_moderation(CHAT_ID, MESSAGE_ID)
    await store_result_id(CHAT_ID, MESSAGE_ID, "review-msg-77")

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["result_id"] == "review-msg-77"

@pytest.mark.asyncio
async def test_mark_failed_is_recorded(patched_db_conn, clean_db):
    """Exhausting every leg is visible rather than looking like a lost row."""
    await claim_pending(CHAT_ID, MESSAGE_ID)
    await mark_failed(CHAT_ID, MESSAGE_ID)

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["status"] == "failed"
    assert row["is_spam"] is None

@pytest.mark.asyncio
async def test_ensure_table_is_idempotent(patched_db_conn, clean_db):
    """The DDL can run repeatedly - the boot hook runs it on every start."""
    async with clean_db.acquire() as conn:
        await ensure_verdict_table(conn)
        await ensure_verdict_table(conn)

    assert await claim_pending(CHAT_ID, MESSAGE_ID) is True

@pytest.mark.asyncio
async def test_cleanup_old_verdicts_deletes(patched_db_conn, clean_db):
    """A future cutoff makes every row older than it: all are reaped."""
    await claim_pending(CHAT_ID, MESSAGE_ID)
    await claim_pending(CHAT_ID, MESSAGE_ID + 1)

    deleted = await cleanup_old_verdicts(days=-1)
    assert deleted == 2
    assert await claim_or_read(CHAT_ID, MESSAGE_ID) is None

@pytest.mark.asyncio
async def test_cleanup_stale_pending_spares_decided(patched_db_conn, clean_db):
    """Only stale PENDING rows are reaped; a decided verdict is kept."""
    await claim_pending(CHAT_ID, MESSAGE_ID)
    await claim_pending(CHAT_ID, MESSAGE_ID + 1)
    await store_verdict(CHAT_ID, MESSAGE_ID + 1, False, 10, "ham")

    deleted = await cleanup_stale_pending_verdicts(minutes=-1)
    assert deleted == 1
    assert await claim_or_read(CHAT_ID, MESSAGE_ID) is None
    assert await claim_or_read(CHAT_ID, MESSAGE_ID + 1) is not None

@pytest.mark.asyncio
async def test_both_cleanups_are_wired_into_scheduled_jobs(
    patched_db_conn, clean_db, monkeypatch
):
    """The store is only reaped if the scheduled job actually calls the cleanups."""
    calls: list[str] = []

    async def _spy_old(days):
        calls.append(f"old:{days}")
        return 0

    async def _spy_stale(minutes):
        calls.append(f"stale:{minutes}")
        return 0

    monkeypatch.setattr(scheduled_tasks, "cleanup_old_verdicts", _spy_old)
    monkeypatch.setattr(scheduled_tasks, "cleanup_stale_pending_verdicts", _spy_stale)
    await scheduled_tasks.run_scheduled_jobs()

    assert f"old:{DEFAULT_VERDICT_TTL_DAYS}" in calls
    assert f"stale:{DEFAULT_PENDING_STALE_MINUTES}" in calls

def test_ttl_defaults_match_config_keys():
    """The scheduled job reads its own config keys with the documented defaults."""
    ttl = scheduled_tasks._get_cache_ttl_days()
    assert ttl["verdict_ttl_days"] == DEFAULT_VERDICT_TTL_DAYS
    assert ttl["verdict_pending_stale_minutes"] == DEFAULT_PENDING_STALE_MINUTES

@pytest.mark.asyncio
async def test_failed_attempt_increments_attempts(patched_db_conn, clean_db):
    """Each exhausted attempt is counted: the cap is what bounds the retry."""
    await claim_pending(CHAT_ID, MESSAGE_ID)

    await mark_failed(CHAT_ID, MESSAGE_ID)
    assert (await claim_or_read(CHAT_ID, MESSAGE_ID))["attempts"] == 1

    await mark_failed(CHAT_ID, MESSAGE_ID)
    assert (await claim_or_read(CHAT_ID, MESSAGE_ID))["attempts"] == 2

async def _fail_at(pool, when: datetime) -> None:
    """Mark the row failed, with `decided_at` set to an EXPLICIT instant.

    The explicit write is load-bearing, not tidiness. `mark_failed` sets
    `decided_at = NOW()`, which SQLite renders as `2026-09-26 18:06:47` (a
    SPACE), while conftest's `register_adapter(datetime, isoformat)` binds a
    parameter as `2026-09-26T17:06:47+00:00` (a T). Space sorts below T, so
    comparing a NOW()-written column against a bound datetime is TRUE whatever
    the two instants are - the cooldown would read as "always expired" and the
    test would pass or fail for a formatting reason rather than the predicate.
    Writing both sides through the same adapter keeps the comparison ordered.
    PostgreSQL compares true timestamptz and is unaffected.
    """
    await claim_pending(CHAT_ID, MESSAGE_ID)
    await mark_failed(CHAT_ID, MESSAGE_ID)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE classification_verdicts SET decided_at = $1 "
            "WHERE chat_id = $2 AND message_id = $3",
            when,
            CHAT_ID,
            MESSAGE_ID,
        )

@pytest.mark.asyncio
async def test_reclaim_failed_hands_the_row_back_for_retry(patched_db_conn, clean_db):
    """A failure older than the cooldown is claimable again, and becomes pending."""
    await _fail_at(clean_db, datetime.now(UTC) - timedelta(hours=2))

    won = await reclaim_failed(CHAT_ID, MESSAGE_ID, cooldown=timedelta(hours=1))
    assert won is True
    assert (await claim_or_read(CHAT_ID, MESSAGE_ID))["status"] == "pending"

@pytest.mark.asyncio
async def test_reclaim_failed_loses_inside_the_cooldown(patched_db_conn, clean_db):
    """Too soon after the failure the row is left alone - this is the backoff."""
    await _fail_at(clean_db, datetime.now(UTC) - timedelta(minutes=1))

    won = await reclaim_failed(CHAT_ID, MESSAGE_ID, cooldown=timedelta(hours=1))
    assert won is False
    assert (await claim_or_read(CHAT_ID, MESSAGE_ID))["status"] == "failed"

@pytest.mark.asyncio
async def test_reclaim_failed_loses_at_the_attempt_cap(patched_db_conn, clean_db):
    """At the cap the retry stops, which is what ends it instead of looping."""
    await claim_pending(CHAT_ID, MESSAGE_ID)
    for _ in range(DEFAULT_VERDICT_RETRY_ATTEMPTS):
        await mark_failed(CHAT_ID, MESSAGE_ID)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE classification_verdicts SET decided_at = $1 "
            "WHERE chat_id = $2 AND message_id = $3",
            datetime.now(UTC) - timedelta(hours=2), CHAT_ID, MESSAGE_ID,
        )

    won = await reclaim_failed(CHAT_ID, MESSAGE_ID, cooldown=timedelta(hours=1))
    assert won is False
    assert (await claim_or_read(CHAT_ID, MESSAGE_ID))["attempts"] == (
        DEFAULT_VERDICT_RETRY_ATTEMPTS
    )

@pytest.mark.asyncio
async def test_reclaim_wins_exactly_once(patched_db_conn, clean_db):
    """The UPDATE is the claim: a second reclaimer matches nothing."""
    await _fail_at(clean_db, datetime.now(UTC) - timedelta(hours=2))

    first = await reclaim_failed(CHAT_ID, MESSAGE_ID, cooldown=timedelta(hours=1))
    second = await reclaim_failed(CHAT_ID, MESSAGE_ID, cooldown=timedelta(hours=1))
    assert first is True
    assert second is False

@pytest.mark.asyncio
async def test_reclaim_leaves_decided_and_absent_rows_alone(patched_db_conn, clean_db):
    """Only a FAILED row is reclaimable - a verdict is never re-asked."""
    await claim_pending(CHAT_ID, MESSAGE_ID)
    await store_verdict(CHAT_ID, MESSAGE_ID, True, 97, "scam")

    assert (
        await reclaim_failed(CHAT_ID, MESSAGE_ID, cooldown=timedelta(hours=-1))
    ) is False
    assert (await claim_or_read(CHAT_ID, MESSAGE_ID))["status"] == "decided"

    assert (
        await reclaim_failed(CHAT_ID, MESSAGE_ID + 1, cooldown=timedelta(hours=-1))
    ) is False
    assert await claim_or_read(CHAT_ID, MESSAGE_ID + 1) is None

@pytest.mark.asyncio
async def test_ensure_table_adds_attempts_to_a_legacy_table(patched_db_conn, clean_db):
    """The upgrade path: an existing table gains the column on the boot hook.

    The live store predates `attempts`, and CREATE TABLE IF NOT EXISTS is a
    no-op against it, so without the ALTER the deployed column never appears.
    """
    async with clean_db.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS classification_verdicts")
        await conn.execute(
            """
            CREATE TABLE classification_verdicts (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                is_spam BOOLEAN,
                confidence INTEGER,
                reason TEXT,
                result_id TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                decided_at TIMESTAMP,
                moderated_at TIMESTAMP,
                UNIQUE(chat_id, message_id)
            )
            """
        )
        await ensure_verdict_table(conn)
        await ensure_verdict_table(conn)

    # Functional proof rather than a schema read: claim_or_read selects the
    # column, so this passes only if the ALTER actually landed.
    assert await claim_pending(CHAT_ID, MESSAGE_ID) is True
    assert (await claim_or_read(CHAT_ID, MESSAGE_ID))["attempts"] == 0
