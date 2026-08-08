"""
Message lookup cache for recovering original message metadata from forwarded messages.

Stores message metadata (chat_id, message_id, user_id, text, reply_to_text) and
classification context (stories, account_signals) for lookup when admins forward
messages to add spam examples. Replaces Logfire-based lookup.
"""

import logging
import re
from collections.abc import Sequence
from datetime import datetime

from .postgres_connection import get_pool

logger = logging.getLogger(__name__)

DEFAULT_LOOKUP_TTL_DAYS = 7


def _build_text_like_pattern(message_text: str) -> str:
    """Build LIKE pattern from message text. Joins first 10 words with % for robust matching."""
    first_paragraph = message_text.split("\n\n")[0][:150]
    if words := re.findall(r"\w+", first_paragraph):
        return "%".join(words[:10])
    return message_text[:100].replace("%", "\\%")


async def save_message_lookup_entry(
    chat_id: int,
    message_id: int,
    effective_user_id: int,
    message_text: str,
    *,
    reply_to_text: str | None = None,
    stories_context: str | None = None,
    account_signals_context: str | None = None,
) -> None:
    """Upsert message metadata and optional classification context into lookup cache."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO message_lookup_cache (
                chat_id, message_id, effective_user_id, message_text,
                reply_to_text, stories_context, account_signals_context
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (chat_id, message_id) DO UPDATE SET
                effective_user_id = EXCLUDED.effective_user_id,
                message_text = EXCLUDED.message_text,
                reply_to_text = COALESCE(EXCLUDED.reply_to_text, message_lookup_cache.reply_to_text),
                stories_context = COALESCE(EXCLUDED.stories_context, message_lookup_cache.stories_context),
                account_signals_context = COALESCE(EXCLUDED.account_signals_context, message_lookup_cache.account_signals_context),
                created_at = NOW()
            """,
            chat_id,
            message_id,
            effective_user_id,
            message_text[:10000],  # Limit length
            reply_to_text[:5000] if reply_to_text else None,  # Limit length
            stories_context[:10000] if stories_context else None,
            account_signals_context[:2000] if account_signals_context else None,
        )


async def find_message_by_text_and_user(
    message_text: str,
    admin_chat_ids: Sequence[int],
    from_date: datetime,
    to_date: datetime,
    *,
    user_id: int | None = None,
) -> dict | None:
    """Find most recent message in lookup cache by text, optional user_id, and date range."""
    if not admin_chat_ids:
        return None

    pattern = _build_text_like_pattern(message_text)
    pattern_escaped = pattern.replace("'", "''").replace("\\", "\\\\")

    pool = await get_pool()
    async with pool.acquire() as conn:
        if user_id is not None:
            row = await conn.fetchrow(
                """
                SELECT chat_id, message_id, effective_user_id, reply_to_text,
                       stories_context, account_signals_context
                FROM message_lookup_cache
                WHERE message_text LIKE $1
                  AND effective_user_id = $2
                  AND chat_id = ANY($3)
                  AND created_at >= $4
                  AND created_at <= $5
                ORDER BY created_at DESC
                LIMIT 1
                """,
                f"%{pattern_escaped}%",
                user_id,
                list(admin_chat_ids),
                from_date,
                to_date,
            )
        else:
            row = await conn.fetchrow(
                """
                SELECT chat_id, message_id, effective_user_id, reply_to_text,
                       stories_context, account_signals_context
                FROM message_lookup_cache
                WHERE message_text LIKE $1
                  AND chat_id = ANY($2)
                  AND created_at >= $3
                  AND created_at <= $4
                ORDER BY created_at DESC
                LIMIT 1
                """,
                f"%{pattern_escaped}%",
                list(admin_chat_ids),
                from_date,
                to_date,
            )

        if row is None:
            return None

        return {
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "user_id": row["effective_user_id"],
            "reply_to_text": row["reply_to_text"],
            "stories_context": row["stories_context"],
            "account_signals_context": row["account_signals_context"],
        }


async def cleanup_old_lookup_entries(days: int = DEFAULT_LOOKUP_TTL_DAYS) -> int:
    """Remove entries older than specified days. Returns deleted count."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM message_lookup_cache
            WHERE created_at < NOW() - INTERVAL '1 day' * $1
            """,
            days,
        )
    count = int(result.split()[-1]) if result else 0
    if count > 0:
        logger.info(f"Cleaned up {count} old message_lookup_cache entries")
    return count
