"""Shared MTProto history-fetch helpers.

Extracted from user_profile.py so both collect_channel_summary_by_id and
the chat_topics scan service can reuse the same fetch/parse logic without
circular imports.

No behaviour change — pure mechanical extraction.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from ..common.mtproto_client import MtprotoHttpClient, MtprotoHttpError

logger = logging.getLogger(__name__)

# Type alias matching user_profile.py
JsonDict = dict[str, Any]


# ---------------------------------------------------------------------------
# Pure helpers (no I/O)
# ---------------------------------------------------------------------------


def build_history_params(
    *, peer_reference: int | str, add_offset: int, limit: int
) -> JsonDict:
    """Build the params dict for messages.getHistory."""
    return {
        "peer": peer_reference,
        "offset_id": 0,
        "offset_date": 0,
        "add_offset": add_offset,
        "limit": limit,
        "max_id": 0,
        "min_id": 0,
        "hash": 0,
    }


def extract_message_text(message: dict[str, Any]) -> str:
    """Extract text content from a Telegram message dict."""
    if not message:
        return ""

    # Direct message text
    message_text = message.get("message", "")

    # Caption from media messages
    if not message_text:
        media = message.get("media")
        if media and isinstance(media, dict):
            message_text = media.get("caption", "")

    return message_text


def extract_date(timestamp: Any) -> datetime | None:
    """Parse an MTProto timestamp (int epoch or ISO-8601 string) to datetime."""
    if not timestamp:
        return None
    if isinstance(timestamp, int):
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(timestamp, str):
        try:
            return datetime.fromisoformat(timestamp)
        except ValueError:
            logger.debug(
                "Failed to parse date from timestamp",
                extra={"timestamp": timestamp},
            )
            return None
    return None


def extract_message_date(message: dict[str, Any] | None) -> datetime | None:
    """Convenience: extract the date from a message dict."""
    return extract_date(message.get("date")) if message else None


def extract_first_message_and_total(
    history: JsonDict,
) -> tuple[JsonDict | None, int | None]:
    """Parse messages.getHistory response into (first_message, total_count)."""
    messages = history.get("messages", [])
    first_message = messages[0] if messages else None
    total = history.get("count")
    if total is None and messages:
        total = len(messages)
    return first_message, total


# ---------------------------------------------------------------------------
# Async fetchers (need the MTProto client)
# ---------------------------------------------------------------------------


async def fetch_recent_posts_content(
    client: MtprotoHttpClient,
    peer_reference: int | str,
    limit: int = 5,
) -> tuple[list[str], JsonDict | None, JsonDict | None, int | None]:
    """Fetch content from recent posts in a channel/group.

    Returns:
        (content_list, newest_message, oldest_message_in_batch, total_count)
    """
    params = build_history_params(
        peer_reference=peer_reference, add_offset=0, limit=limit
    )

    try:
        history = await client.call("messages.getHistory", params=params, resolve=True)
    except MtprotoHttpError as exc:
        logger.info(
            "Failed to fetch recent posts content",
            extra={"peer_reference": peer_reference, "error": str(exc)},
        )
        return [], None, None, None

    messages = history.get("messages", [])
    content_list: list[str] = []
    newest_message = messages[0] if messages else None
    oldest_message_in_batch = messages[-1] if len(messages) > 1 else newest_message
    total_count = history.get("count")

    for message in messages:
        text_content = extract_message_text(message)
        if text_content and text_content.strip():
            content_list.append(text_content.strip())

    return content_list, newest_message, oldest_message_in_batch, total_count


async def fetch_channel_edge_message(
    client: MtprotoHttpClient,
    peer_reference: int | str,
    *,
    limit_offset: int | None,
) -> tuple[JsonDict | None, int | None]:
    """Fetch a single message at a specific offset (for edge-post dates)."""
    params = build_history_params(
        peer_reference=peer_reference,
        add_offset=max(limit_offset or 0, 0),
        limit=1,
    )

    try:
        history = await client.call("messages.getHistory", params=params, resolve=True)
    except MtprotoHttpError as exc:
        logger.info(
            "Failed to fetch channel history",
            extra={"peer_reference": peer_reference, "error": str(exc)},
        )
        return None, None

    return extract_first_message_and_total(history)


async def fetch_recent_messages(
    client: MtprotoHttpClient,
    peer_reference: int | str,
    limit: int,
) -> tuple[list[JsonDict], int | None, MtprotoHttpError | None]:
    """Fetch raw recent messages (newest-first) plus total count.

    Unlike fetch_recent_posts_content, this does NOT swallow errors: the
    caller (chat_topics scan) needs to distinguish a failed channel read
    (fall back to the discussion group) from a successful one. Returns
    (messages, total_count, error). On error, messages=[] and error set.
    """
    params = build_history_params(
        peer_reference=peer_reference, add_offset=0, limit=limit
    )

    try:
        history = await client.call("messages.getHistory", params=params, resolve=True)
    except MtprotoHttpError as exc:
        logger.info(
            "Failed to fetch recent messages",
            extra={"peer_reference": peer_reference, "error": str(exc)},
        )
        return [], None, exc

    messages: list[JsonDict] = history.get("messages", [])
    total_count = history.get("count")
    return messages, total_count, None
