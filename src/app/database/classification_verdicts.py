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

`moderated_at` is the moderation claim, independent of `status`: it is set by
`claim_moderation` with an `IS NULL` guard, so exactly one caller wins even if
two deliveries race.
"""

import logging
from datetime import UTC, datetime, timedelta

from .postgres_connection import get_pool

logger = logging.getLogger(__name__)

DEFAULT_VERDICT_TTL_DAYS = 7
DEFAULT_PENDING_STALE_MINUTES = 15

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
    UNIQUE(chat_id, message_id)
)
"""

VERDICT_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_classification_verdicts_created
    ON classification_verdicts(created_at)
"""

async def ensure_verdict_table(conn) -> None:
    """Create the verdict table and its index. Idempotent."""
    await conn.execute(VERDICT_TABLE_DDL)
    await conn.execute(VERDICT_INDEX_DDL)

async def claim_or_read(chat_id: int, message_id: int) -> dict | None:
    """Return the stored row for this message, or None if there is none."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT chat_id, message_id, status, is_spam, confidence, reason,
                   result_id, moderated_at
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
    """Record that every leg was exhausted. No verdict exists."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE classification_verdicts
            SET status = 'failed', decided_at = NOW()
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

    A pending row is DELETED rather than marked: the detached task is bounded at
    roughly the classification budget plus the session-middleware retries inside
    moderation, so 15 minutes is ~10x the maximum lifetime. Deleting restores
    the `absent` path, so a later delivery re-classifies - whereas leaving it
    would make every redelivery answer 503 forever.
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
