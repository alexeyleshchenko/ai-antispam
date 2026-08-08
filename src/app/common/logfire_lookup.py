"""Logfire query utilities. get_weekly_stats for admin dashboard; message lookup is PostgreSQL."""

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

LogfireQueryClient: Any = None

try:
    from logfire.query_client import LogfireQueryClient

    _logfire_import_error: Exception | None = None
except ImportError as e:
    # Logfire is an optional dependency in production. When it's not installed we
    # still want /stats to work and simply return zeroes instead of crashing.
    _logfire_import_error = e

logger = logging.getLogger(__name__)

_client: LogfireQueryClient | None = None


def _get_client() -> LogfireQueryClient:
    """Get or create the LogfireQueryClient singleton.

    This function is intentionally tolerant of missing logfire in production:
    when the import failed at module import time we surface a clear exception
    that callers of get_weekly_stats catch and degrade to zero stats.
    """
    global _client

    if _logfire_import_error is not None:
        raise RuntimeError(
            "logfire is not installed, weekly stats queries are disabled"
        ) from _logfire_import_error

    if _client is None:
        import os

        if token := os.getenv("LOGFIRE_READ_TOKEN"):
            _client = LogfireQueryClient(token)  # type: ignore[call-arg]
        else:
            raise ValueError(
                "LOGFIRE_READ_TOKEN environment variable is required for Logfire queries. "
                "Please set it to your Logfire read token."
            )

    return _client


async def get_weekly_stats(chat_ids: Sequence[int]) -> dict[int, dict[str, int]]:
    """Query Logfire for weekly stats (last 7 days). Returns dict of chat_id -> {processed, spam}."""
    if not chat_ids:
        return {}

    start_time = datetime.now(UTC) - timedelta(days=7)
    chat_ids_str = ", ".join(f"'{chat_id}'" for chat_id in chat_ids)

    # Tags indicating different outcomes
    spam_tags = {"spam_auto_deleted", "spam_admins_notified"}
    processed_tags = {
        "message_user_approved",
        "message_trusted_member_skipped",
        "message_insufficient_credits",
        "message_spam_check_failed",
        "message_from_group_admin_skipped",
        "message_from_channel_bot_skipped",
        "message_from_admin_skipped",
    } | spam_tags

    sql = f"""
    SELECT
        (attributes->'update'->'message'->'chat'->>'id')::bigint as chat_id,
        tags,
        count(*) as count
    FROM records
    WHERE
        start_timestamp >= '{start_time.isoformat()}'
        AND (attributes->'update'->'message'->'chat'->>'id')::bigint IN ({chat_ids_str})
    GROUP BY 1, 2
    """

    stats: dict[int, dict[str, int]] = {
        chat_id: {"processed": 0, "spam": 0} for chat_id in chat_ids
    }

    try:
        client = _get_client()
        results = await asyncio.to_thread(
            client.query_json_rows,
            sql=sql,
            min_timestamp=start_time,
        )

        if results and results.get("rows"):
            for row in results["rows"]:
                chat_id_val = row.get("chat_id")
                if chat_id_val is None:
                    continue
                chat_id = int(chat_id_val)

                tags = row.get("tags") or []
                count = int(row.get("count", 0))

                if chat_id not in stats:
                    continue

                # Check tags
                is_spam = any(t in spam_tags for t in tags)
                is_processed = any(t in processed_tags for t in tags)

                if is_spam:
                    stats[chat_id]["spam"] += count

                if is_processed:
                    stats[chat_id]["processed"] += count

        return stats

    except Exception as e:
        logger.warning(f"Failed to get weekly stats from Logfire: {e}", exc_info=True)
        return stats
