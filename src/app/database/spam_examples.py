import logging
from typing import Any, Dict, List, Optional

import logfire

from ..common.utils import clean_alert_text, load_config
from .postgres_connection import get_pool

logger = logging.getLogger(__name__)

PENDING_SCORE = -100
SPAM_CONFIRMED_SCORE = 100


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def insert_pending_spam_example(
    chat_id: int,
    message_id: int,
    effective_user_id: int,
    *,
    text: str = "[MEDIA_MESSAGE]",
    name: Optional[str] = None,
    bio: Optional[str] = None,
    linked_channel_fragment: Optional[str] = None,
    stories_context: Optional[str] = None,
    reply_context: Optional[str] = None,
    account_signals_context: Optional[str] = None,
) -> int:
    """
    Insert a pending spam example. Run TTL cleanup, return new row id.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO spam_examples (
                    text, name, bio, score,
                    linked_channel_fragment, stories_context, reply_context, account_signals_context,
                    confirmed, chat_id, message_id, effective_user_id
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, false, $9, $10, $11)
                ON CONFLICT (chat_id, message_id) WHERE confirmed = false
                DO UPDATE SET
                    text = EXCLUDED.text,
                    name = EXCLUDED.name,
                    bio = EXCLUDED.bio,
                    score = EXCLUDED.score,
                    linked_channel_fragment = EXCLUDED.linked_channel_fragment,
                    stories_context = EXCLUDED.stories_context,
                    reply_context = EXCLUDED.reply_context,
                    account_signals_context = EXCLUDED.account_signals_context,
                    effective_user_id = EXCLUDED.effective_user_id,
                    created_at = NOW()
                RETURNING id
                """,
                text,
                name,
                bio,
                PENDING_SCORE,
                linked_channel_fragment,
                stories_context,
                reply_context,
                account_signals_context,
                chat_id,
                message_id,
                effective_user_id,
            )
            return row["id"]


async def cleanup_pending_spam_examples(days: int = 3) -> int:
    """
    Remove stale pending spam examples (confirmed=false, older than days).
    Returns deleted count.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM spam_examples
            WHERE confirmed = false
            AND created_at < NOW() - INTERVAL '1 day' * $1
            """,
            days,
        )
    count = int(result.split()[-1]) if result else 0
    if count > 0:
        logger.info(f"Cleaned up {count} stale pending spam examples")
    return count


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def confirm_pending_example_as_not_spam(
    pending_id: int, admin_id: int
) -> Optional[Dict[str, Any]]:
    """Mark pending example as not spam. Returns chat_id, message_id, effective_user_id or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE spam_examples
            SET confirmed = true, admin_id = $1
            WHERE id = $2 AND confirmed = false
            RETURNING chat_id, message_id, effective_user_id
            """,
            admin_id,
            pending_id,
        )
        if not row or row["chat_id"] is None:
            return None
        return {
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "effective_user_id": row["effective_user_id"],
        }


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def confirm_pending_example_as_spam(
    chat_id: int, message_id: int, admin_id: int
) -> bool:
    """
    Mark pending row as confirmed spam (score=100, admin_id). Returns True if
    updated, False if no matching pending row.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE spam_examples
            SET confirmed = true, admin_id = $1, score = $2
            WHERE chat_id = $3 AND message_id = $4 AND confirmed = false
            """,
            admin_id,
            SPAM_CONFIRMED_SCORE,
            chat_id,
            message_id,
        )
        return result != "UPDATE 0"


async def get_pending_example_by_message(
    chat_id: int, message_id: int
) -> Optional[int]:
    """Find pending spam example ID by chat and message ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT id FROM spam_examples
            WHERE chat_id = $1 AND message_id = $2 AND confirmed = false
            LIMIT 1
            """,
            chat_id,
            message_id,
        )


def _get_examples_config() -> tuple[int, float, float]:
    """Return (limit, ham_ratio, spam_ratio) from config. spam_ratio = 1 - ham_ratio."""
    spam_cfg = load_config().get("spam", {})
    limit = spam_cfg.get("examples_limit", 40)
    ham_ratio = spam_cfg.get("examples_ham_ratio", 0.25)
    spam_ratio = 1.0 - ham_ratio
    return limit, ham_ratio, spam_ratio


async def get_spam_examples(
    admin_ids: Optional[List[int]] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Get spam examples from PostgreSQL with proportional ham/spam mix.
    With admin_ids, includes user-specific examples.
    Uses examples_limit and examples_ham_ratio / examples_spam_ratio from config.
    Prefers most recent examples within each category."""
    cfg_limit, ham_ratio, spam_ratio = _get_examples_config()
    total_limit = limit if limit is not None else cfg_limit
    ham_limit = max(1, round(total_limit * ham_ratio))
    spam_limit = max(1, round(total_limit * spam_ratio))

    pool = await get_pool()
    async with pool.acquire() as conn:
        if admin_ids:
            ham_rows = await conn.fetch(
                """
                SELECT text, name, bio, score, linked_channel_fragment, stories_context, reply_context, account_signals_context, created_at
                FROM spam_examples
                WHERE (admin_id IS NULL OR admin_id = ANY($1)) AND (confirmed IS NOT DISTINCT FROM true) AND score < 0
                ORDER BY created_at DESC
                LIMIT $2
                """,
                admin_ids,
                ham_limit,
            )
            spam_rows = await conn.fetch(
                """
                SELECT text, name, bio, score, linked_channel_fragment, stories_context, reply_context, account_signals_context, created_at
                FROM spam_examples
                WHERE (admin_id IS NULL OR admin_id = ANY($1)) AND (confirmed IS NOT DISTINCT FROM true) AND score > 0
                ORDER BY created_at DESC
                LIMIT $2
                """,
                admin_ids,
                spam_limit,
            )
        else:
            ham_rows = await conn.fetch(
                """
                SELECT text, name, bio, score, linked_channel_fragment, stories_context, reply_context, account_signals_context, created_at
                FROM spam_examples
                WHERE admin_id IS NULL AND (confirmed IS NOT DISTINCT FROM true) AND score < 0
                ORDER BY created_at DESC
                LIMIT $1
                """,
                ham_limit,
            )
            spam_rows = await conn.fetch(
                """
                SELECT text, name, bio, score, linked_channel_fragment, stories_context, reply_context, account_signals_context, created_at
                FROM spam_examples
                WHERE admin_id IS NULL AND (confirmed IS NOT DISTINCT FROM true) AND score > 0
                ORDER BY created_at DESC
                LIMIT $1
                """,
                spam_limit,
            )

    combined = list(ham_rows) + list(spam_rows)
    combined.sort(key=lambda r: r["created_at"], reverse=True)

    return [
        {
            "text": row["text"],
            "name": row["name"],
            "bio": row["bio"],
            "score": row["score"],
            "linked_channel_fragment": row["linked_channel_fragment"],
            "stories_context": row["stories_context"],
            "reply_context": row["reply_context"],
            "account_signals_context": row["account_signals_context"],
        }
        for row in combined
    ]


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def add_spam_example(
    text: str,
    score: int,
    name: Optional[str] = None,
    bio: Optional[str] = None,
    admin_id: Optional[int] = None,
    linked_channel_fragment: Optional[str] = None,
    stories_context: Optional[str] = None,
    reply_context: Optional[str] = None,
    account_signals_context: Optional[str] = None,
) -> bool:
    """Add a new spam example to PostgreSQL"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            try:
                cleaned_text = clean_alert_text(text)
                await conn.execute(
                    """
                    DELETE FROM spam_examples
                    WHERE text = $1 AND (name = $2 OR (name IS NULL AND $2 IS NULL))
                    AND (admin_id = $3 OR (admin_id IS NULL AND $3 IS NULL))
                    AND (confirmed IS NOT DISTINCT FROM true)
                """,
                    cleaned_text,
                    name,
                    admin_id,
                )

                await conn.execute(
                    """
                    INSERT INTO spam_examples (
                        text,
                        name,
                        bio,
                        score,
                        admin_id,
                        linked_channel_fragment,
                        stories_context,
                        reply_context,
                        account_signals_context,
                        created_at
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                """,
                    cleaned_text,
                    name,
                    bio,
                    score,
                    admin_id,
                    linked_channel_fragment,
                    stories_context,
                    reply_context,
                    account_signals_context,
                )
                return True
            except Exception as e:
                logger.error(f"Error adding spam example: {e}")
                return False
