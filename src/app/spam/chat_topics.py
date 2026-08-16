"""Chat-topic scan service.

Phase 1 (manual): every scan is triggered by the admin's /scan command.
Retrieves recent messages from a monitored chat, derives a topic profile
via the LLM (gateway -> OpenRouter rotation), and stores it on the `groups`
row for use by the spam classifier (/stats shows the short form).

Failure semantics (design doc §4):
- Any fetch/derive/DB failure -> row untouched, FAILED returned, admin re-runs /scan.
- First scan (topic fields NULL) that fails at derivation -> chat title fallback
  (state never empty).
- Re-scan that fails -> keep the existing description, never clobber with a title.
- Empty sample (media-only chat) -> skip the LLM call entirely: use the title on
  first scan, keep existing otherwise.
"""

import logging
from dataclasses import dataclass

import logfire

from ..common.mtproto_client import (
    MtprotoHttpClient,
    MtprotoHttpError,
    get_mtproto_client,
)
from ..common.mtproto_utils import bot_api_chat_id_to_mtproto
from ..common.utils import format_chat_log, load_config
from ..database.postgres_connection import get_pool
from ..agents import derive_topic_summary, topic_summary_from_title
from .mtproto_history import (
    extract_message_text,
    fetch_recent_messages,
)

logger = logging.getLogger(__name__)


@dataclass
class ChatTopicScanResult:
    """Outcome of a chat-topic scan."""

    status: (
        str  # "ok" | "failed" | "empty_first_scan" | "kept_existing" | "title_fallback"
    )
    detail: str = ""


def _topic_scan_limit() -> int:
    """Fetch cap — how many recent messages we pull from MTProto."""
    limit = load_config().get("spam", {}).get("topic_scan_limit", 100)
    return limit if isinstance(limit, int) and limit > 0 else 100


def _max_message_chars() -> int:
    """Per-message truncation cap (head kept — topic signal at the start)."""
    limit = load_config().get("spam", {}).get("topic_max_message_chars", 500)
    return limit if isinstance(limit, int) and limit > 0 else 500


def _max_total_chars() -> int:
    """Total corpus budget for the LLM input (newest-first, past-budget dropped)."""
    limit = load_config().get("spam", {}).get("topic_max_total_chars", 16_000)
    return limit if isinstance(limit, int) and limit > 0 else 16_000


async def _load_group_row(group_id: int) -> dict | None:
    """Load the fields the scan needs from the groups row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT group_id, title, username, linked_channel_id,
                   topic_description, topic_description_short, topic_updated_at
            FROM groups WHERE group_id = $1
            """,
            group_id,
        )
        return dict(row) if row else None


async def _save_topic(
    group_id: int,
    description: str | None,
    short_description: str | None,
) -> None:
    """Write the topic profile. description=None -> only update timestamp."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE groups
            SET topic_description = COALESCE($1, topic_description),
                topic_description_short = COALESCE($2, topic_description_short),
                topic_updated_at = NOW()
            WHERE group_id = $3
            """,
            description,
            short_description,
            group_id,
        )


def _is_bot_own_message(message: dict) -> bool:
    """Messages sent by the bot session itself (MTProto `out` flag)."""
    return bool(message.get("out"))


def _is_command_message(message: dict) -> bool:
    """Obvious admin commands — text starting with '/'."""
    text = extract_message_text(message)
    return bool(text and text.startswith("/"))


def _trim_sample(messages: list[dict], max_msg: int, max_total: int) -> list[str]:
    """Truncate/dedupe newest-first to a hard char budget.

    Per-message cap keeps the head (topic signal at start); the total budget
    stops adding once exceeded; past-budget messages are dropped, not errors.
    """
    seen: set[str] = set()
    parts: list[str] = []
    total = 0

    for message in messages:  # fetch_recent_messages returns newest-first
        text = extract_message_text(message)
        if not text or not text.strip():
            continue
        trimmed = text.strip()[:max_msg]
        if trimmed in seen:
            continue
        seen.add(trimmed)
        if total + len(trimmed) > max_total:
            break
        parts.append(trimmed)
        total += len(trimmed)

    return parts


async def _fetch_sample(
    client: MtprotoHttpClient,
    peer: int | str,
    limit: int,
) -> tuple[list[str], MtprotoHttpError | None]:
    """Fetch raw messages and trim to the LLM budget."""
    messages, _total, error = await fetch_recent_messages(client, peer, limit)
    if error:
        return [], error

    max_msg = _max_message_chars()
    max_total = _max_total_chars()
    sample = _trim_sample(messages, max_msg=max_msg, max_total=max_total)
    return sample, None


async def scan_chat_topics(group_id: int) -> ChatTopicScanResult:
    """Derive and store a topic profile for a monitored chat.

    Returns ChatTopicScanResult — never raises. Any failure leaves the row
    untouched so the classifier behaves exactly as before (no topic signal).
    """
    row = await _load_group_row(group_id)
    if row is None:
        logger.warning(f"Topic scan: group not found: {format_chat_log(group_id)}")
        return ChatTopicScanResult(status="failed", detail="group_not_found")

    chat_log = format_chat_log(group_id)
    title = row.get("title")
    username = row.get("username")
    linked_channel_id = row.get("linked_channel_id")
    was_scanned_before = (
        row.get("topic_description") is not None
        or row.get("topic_updated_at") is not None
    )

    with logfire.span(
        "scan_chat_topics",
        group_id=group_id,
        chat=chat_log,
        linked_channel_id=linked_channel_id,
    ):
        client = get_mtproto_client()
        limit = _topic_scan_limit()

        # Resolve the fetch peer.
        # Channel-protected discussion (linked_channel_id set): scan the channel
        # peer directly — channel posts are owner-authored by construction (the
        # "filter by channel owner" requirement). Fall back to the discussion
        # group's own messages if the channel peer is unreadable.
        if linked_channel_id is not None:
            channel_peer = bot_api_chat_id_to_mtproto(linked_channel_id)
            sample, error = await _fetch_sample(client, channel_peer, limit)
            if error:
                logger.info(
                    f"Topic scan: channel peer unreadable for {chat_log}, "
                    f"falling back to discussion group",
                    extra={"error": str(error)},
                )
                discussion_peer = username or bot_api_chat_id_to_mtproto(group_id)
                sample, error = await _fetch_sample(client, discussion_peer, limit)
                if error:
                    return ChatTopicScanResult(
                        status="failed", detail="both_peers_unreadable"
                    )
        else:
            # Plain group / discussion: all messages, minus bot-own + commands.
            peer = username or bot_api_chat_id_to_mtproto(group_id)
            messages, _total, error = await fetch_recent_messages(client, peer, limit)
            if error:
                return ChatTopicScanResult(status="failed", detail="fetch_failed")
            raw = [
                m
                for m in messages
                if not _is_bot_own_message(m) and not _is_command_message(m)
            ]
            max_msg = _max_message_chars()
            max_total = _max_total_chars()
            sample = _trim_sample(raw, max_msg=max_msg, max_total=max_total)

        # Empty sample: skip the LLM call entirely.
        if not sample:
            logger.info(f"Topic scan: empty sample for {chat_log}, skipping LLM")
            if was_scanned_before:
                return ChatTopicScanResult(status="empty_kept_existing")
            # First scan: chat title fallback — state never empty.
            fallback = topic_summary_from_title(title or "")
            await _save_topic(
                group_id,
                fallback.description,
                fallback.short_description,
            )
            return ChatTopicScanResult(
                status="empty_first_scan",
                detail=fallback.short_description,
            )

        # Derive via LLM (gateway -> OpenRouter rotation, never raises).
        sample_text = "\n---\n".join(sample)
        summary = await derive_topic_summary(sample_text)

        if summary is None:
            if was_scanned_before:
                logger.warning(
                    f"Topic scan: derivation failed for {chat_log}, "
                    "keeping existing description"
                )
                return ChatTopicScanResult(status="kept_existing")
            # First scan: chat title fallback — state never empty.
            logger.warning(
                f"Topic scan: derivation failed for {chat_log}, "
                f"falling back to chat title"
            )
            fallback = topic_summary_from_title(title or "")
            await _save_topic(
                group_id,
                fallback.description,
                fallback.short_description,
            )
            return ChatTopicScanResult(
                status="title_fallback",
                detail=fallback.short_description,
            )

        await _save_topic(
            group_id,
            summary.description,
            summary.short_description,
        )
        logger.info(
            f"Topic scan: stored topic for {chat_log}",
            extra={"short": summary.short_description},
        )
        return ChatTopicScanResult(
            status="ok",
            detail=summary.short_description,
        )
