"""Tests for the classification verdict store (src/app/database/classification_verdicts.py).

Covers the properties the store exists for:
- a verdict round-trips (claim -> decide -> read);
- exactly ONE caller wins the classification claim, even when two arrive;
- exactly ONE caller wins the moderation claim, however many deliveries arrive;
- the DDL is idempotent;
- both cleanup paths are wired into the scheduled jobs.
"""

import pytest

from app.background_jobs import scheduled_tasks
from app.database.classification_verdicts import (
    DEFAULT_PENDING_STALE_MINUTES,
    DEFAULT_VERDICT_TTL_DAYS,
    claim_moderation,
    claim_or_read,
    claim_pending,
    cleanup_old_verdicts,
    cleanup_stale_pending_verdicts,
    ensure_verdict_table,
    mark_failed,
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
