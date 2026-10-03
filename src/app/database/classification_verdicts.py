"""Classification verdict store.

A decision that outlives the request budget survives it. The webhook guard
cancels the classification in flight when the deadline fires, so the work is
detached (see main.py) and its verdict is persisted here, keyed by
(chat_id, message_id). A redelivery of the same update then SERVES the stored
verdict instead of paying for a second LLM call, and moderation is claimed
exactly once however many deliveries arrive.

States:
    pending    - a worker claimed the row and is classifying
    decided    - a verdict exists (is_spam, confidence, reason)
    failed     - every leg was exhausted; no verdict

`failed` is RETRYABLE, not terminal. Once every leg fails, the caller answers
503 so Telegram redelivers, and a later delivery reclaims the row through
`reclaim_failed` and classifies again from the real update. That update is NOT
lost with the row: the whole object is persisted in Logfire
(`attributes->'update'`), so it outlives this table's own 7-day TTL — which is
why recovery reads Logfire and not this table. `attempts` counts the failures;
`reclaim_failed` refuses past `max_attempts` and inside `cooldown`, so the retry
is bounded rather than a livelock.

`moderated_at` is the moderation claim, independent of `status`: it is set by
`claim_moderation` with an `IS NULL` guard, so exactly one caller wins even if
two deliveries race.

`closed_at` is the RECOVERY verdict, independent of both: it is set when a
recovery pass PROVES there is nothing left to moderate — the message is gone
from the group, or the group's own policy skips it. A closed row is not a live
loss, so it is excluded from `reclaim_failed` and from the loss-watch's
`status='failed' AND moderated_at IS NULL` predicate, which reads "a message is
sitting in a real group, unmoderated" — false once the message is gone.
`closed_reason` names the recovery outcome that closed it. Set only by the
recovery runner (scripts/recover_failed.py), which owns the presence check.
"""

import logging
from datetime import UTC, datetime, timedelta

from .postgres_connection import get_pool

logger = logging.getLogger(__name__)

DEFAULT_VERDICT_TTL_DAYS = 7
DEFAULT_PENDING_STALE_MINUTES = 15
DEFAULT_VERDICT_RETRY_ATTEMPTS = 3
DEFAULT_VERDICT_RETRY_COOLDOWN_SECONDS = 60

VERDICT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS classification_verdicts (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    is_spam BOOLEAN,
    confidence INTEGER,
    reason TEXT,
    result_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    moderated_at TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    closed_at TIMESTAMPTZ,
    closed_reason TEXT,
    UNIQUE(chat_id, message_id)
)
"""

# `CREATE TABLE IF NOT EXISTS` does not alter a table that already exists, so a
# deployed store needs the column added in place. Idempotent, and run on every
# boot beside the DDL so the deploy path self-heals without a manual migration.
VERDICT_ATTEMPTS_DDL = """
ALTER TABLE classification_verdicts
    ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0
"""

# One ALTER per column, matching VERDICT_ATTEMPTS_DDL above: a single
# `ALTER TABLE` with two `ADD COLUMN` clauses is rejected by SQLite and by the
# test harness's IF-NOT-EXISTS emulation, which rewrites one clause per statement.
VERDICT_CLOSED_AT_DDL = """
ALTER TABLE classification_verdicts
    ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ
"""

VERDICT_CLOSED_REASON_DDL = """
ALTER TABLE classification_verdicts
    ADD COLUMN IF NOT EXISTS closed_reason TEXT
"""

VERDICT_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_classification_verdicts_created
    ON classification_verdicts(created_at)
"""


async def ensure_verdict_table(conn) -> None:
    """Create the verdict table, its index and its added columns. Idempotent."""
    await conn.execute(VERDICT_TABLE_DDL)
    await conn.execute(VERDICT_ATTEMPTS_DDL)
    await conn.execute(VERDICT_CLOSED_AT_DDL)
    await conn.execute(VERDICT_CLOSED_REASON_DDL)
    await conn.execute(VERDICT_INDEX_DDL)


async def claim_or_read(chat_id: int, message_id: int) -> dict | None:
    """Return the stored row for this message, or None if there is none."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT chat_id, message_id, status, is_spam, confidence, reason,
                   result_id, moderated_at, attempts
            FROM classification_verdicts
            WHERE chat_id = $1 AND message_id = $2
            """,
            chat_id,
            message_id,
        )
    if row is None:
        return None
    return {
        "chat_id": row["chat_id"],
        "message_id": row["message_id"],
        "status": row["status"],
        "is_spam": row["is_spam"],
        "confidence": row["confidence"],
        "reason": row["reason"],
        "result_id": row["result_id"],
        "moderated_at": row["moderated_at"],
        "attempts": row["attempts"],
    }


async def claim_pending(chat_id: int, message_id: int) -> bool:
    """Claim the right to classify this message. True if THIS caller won.

    One statement decides the winner: `ON CONFLICT DO NOTHING RETURNING id`
    returns a row only to the caller that actually inserted. A read-then-write
    would be a race - two deliveries can both read `absent`.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO classification_verdicts (chat_id, message_id, status)
            VALUES ($1, $2, 'pending')
            ON CONFLICT (chat_id, message_id) DO NOTHING
            RETURNING id
            """,
            chat_id,
            message_id,
        )
    return row is not None


async def reclaim_failed(
    chat_id: int,
    message_id: int,
    cooldown: timedelta | None = None,
    max_attempts: int = DEFAULT_VERDICT_RETRY_ATTEMPTS,
) -> bool:
    """Claim a retry of a FAILED classification. True if THIS caller won.

    The single UPDATE is the whole claim. It matches only a row that is still
    `failed` and NOT closed, is short of the attempt cap, and has been failed
    longer than `cooldown`; the row lock serialises concurrent reclaims, so the
    loser re-evaluates against `pending` and matches nothing. That is the same
    shape as `claim_pending` and `claim_moderation`: no read-then-write, so no
    race.
    """
    if cooldown is None:
        cooldown = timedelta(seconds=DEFAULT_VERDICT_RETRY_COOLDOWN_SECONDS)
    # Cutoff computed here, not as SQL `NOW() - interval`: same shape as
    # cleanup_stale_pending_verdicts, and it keeps the statement portable.
    cutoff = datetime.now(UTC) - cooldown
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE classification_verdicts
            SET status = 'pending'
            WHERE chat_id = $1 AND message_id = $2
              AND status = 'failed'
              AND closed_at IS NULL
              AND attempts < $3
              AND decided_at < $4
            RETURNING id
            """,
            chat_id,
            message_id,
            max_attempts,
            cutoff,
        )
    return row is not None


async def store_verdict(
    chat_id: int,
    message_id: int,
    is_spam: bool,
    confidence: int,
    reason: str,
) -> None:
    """Record a decided verdict."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE classification_verdicts
            SET status = 'decided',
                is_spam = $1,
                confidence = $2,
                reason = $3,
                decided_at = NOW()
            WHERE chat_id = $4 AND message_id = $5
            """,
            is_spam,
            confidence,
            reason,
            chat_id,
            message_id,
        )


async def mark_failed(chat_id: int, message_id: int) -> None:
    """Record that every leg was exhausted for this attempt.

    Increments `attempts` so `reclaim_failed` can bound the retries. A row that
    reaches the cap stays `failed` and the caller stops answering 503, which is
    what ends the retry rather than letting it run forever.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE classification_verdicts
            SET status = 'failed',
                decided_at = NOW(),
                attempts = attempts + 1
            WHERE chat_id = $1 AND message_id = $2
            """,
            chat_id,
            message_id,
        )


async def claim_moderation(chat_id: int, message_id: int) -> bool:
    """Claim the right to moderate this message. True if THIS caller won.

    The `moderated_at IS NULL` guard is the whole mechanism: N deliveries of the
    same update produce exactly one moderation action.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE classification_verdicts
            SET moderated_at = NOW()
            WHERE chat_id = $1 AND message_id = $2 AND moderated_at IS NULL
            RETURNING id
            """,
            chat_id,
            message_id,
        )
    return row is not None


async def release_moderation_claim(chat_id: int, message_id: int) -> None:
    """Undo a moderation claim. Used only when the moderation action raised,
    so a later delivery can retry it instead of the message being stuck."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE classification_verdicts
            SET moderated_at = NULL, result_id = NULL
            WHERE chat_id = $1 AND message_id = $2
            """,
            chat_id,
            message_id,
        )


async def store_result_id(chat_id: int, message_id: int, result_id: str) -> None:
    """Record the id of the moderation result (e.g. the review message)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE classification_verdicts
            SET result_id = $1
            WHERE chat_id = $2 AND message_id = $3
            """,
            result_id,
            chat_id,
            message_id,
        )


async def close_verdict(chat_id: int, message_id: int, reason: str) -> bool:
    """Close a failed row: nothing left to moderate. True if THIS caller won.

    Set by the recovery runner once it has PROVED the message cannot be
    moderated — it is gone from the group, or the group's own policy skips it
    by design. The `closed_at IS NULL` guard makes it idempotent: a second
    recovery pass over the same row changes nothing.

    Closing does NOT touch `status` or `moderated_at`: it is a separate axis,
    so a closed row still reads `failed` and keeps its attempt count. The point
    is that `reclaim_failed` refuses it and the loss-watch does not count it —
    a message that is gone is not "sitting in a real group, unmoderated".
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE classification_verdicts
            SET closed_at = NOW(),
                closed_reason = $1
            WHERE chat_id = $2 AND message_id = $3 AND closed_at IS NULL
            RETURNING id
            """,
            reason,
            chat_id,
            message_id,
        )
    return row is not None

async def cleanup_old_verdicts(days: int = DEFAULT_VERDICT_TTL_DAYS) -> int:
    """Delete verdicts older than `days`. Returns the deleted count."""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM classification_verdicts WHERE created_at < $1",
            cutoff,
        )
    count = int(result.split()[-1]) if result else 0
    if count > 0:
        logger.info(f"Cleaned up {count} old classification_verdicts entries")
    return count


async def cleanup_stale_pending_verdicts(
    minutes: int = DEFAULT_PENDING_STALE_MINUTES,
) -> int:
    """Delete `pending` rows older than `minutes`. Returns the deleted count.

    A pending row is DELETED rather than marked: the detached task is bounded
    well below this threshold, so a row past it cannot have a live owner.
    Deletion restores the `absent` path, so a later delivery re-classifies -
    whereas leaving it would make every redelivery answer 503 forever.

    MEASURED BOUND, not an impression (issue #48). Worst case = the
    classification budget (45 s) + the moderation path's Telegram operations,
    each capped by `stop_after_attempt(4)` giving 3 waits at the flood-control
    cap of 30 s = 90 s per operation; three operations (delete, ban, admin DM)
    = 315 s = 5.25 min. Against the 900 s default that is a **2.86x** margin -
    NOT the "~10x" this docstring previously claimed. So if the retry attempts
    rise, or another Telegram operation joins the moderation path, RE-MEASURE
    rather than assume headroom: the cadence in `scheduled_tasks` is derived
    from this threshold, and the safety argument rests on that margin.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=minutes)
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM classification_verdicts
            WHERE status = 'pending' AND created_at < $1
            """,
            cutoff,
        )
    count = int(result.split()[-1]) if result else 0
    if count > 0:
        logger.info(f"Cleaned up {count} stale pending classification_verdicts")
    return count
