from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import logfire

from ..common.bot import bot
from ..common.mtproto_client import (
    MtprotoHttpClient,
    MtprotoHttpError,
    get_mtproto_client,
)
from ..common.mtproto_utils import bot_api_chat_id_to_mtproto
from ..common.utils import load_config
from ..types import (
    ContextResult,
    ContextStatus,
    LinkedChannelSummary,
    SpamClassificationContext,
    UserAccountInfo,
)
from .linked_channel_mention import extract_first_channel_mention

logger = logging.getLogger(__name__)
JsonDict = dict[str, Any]


def _empty_context_result() -> ContextResult:
    return ContextResult(status=ContextStatus.EMPTY)


def _extract_is_premium(full_user: JsonDict) -> bool | None:
    """Extract is_premium from a users.getFullUser full_user dict.

    The response contains a 'user' object with a direct premium field.
    """
    user = full_user.get("user", {})
    if isinstance(user, dict):
        premium = user.get("premium")
        if premium is not None:
            return bool(premium)
    return None


def _failed_spam_context(error: str) -> SpamClassificationContext:
    failed = ContextResult(status=ContextStatus.FAILED, error=error)
    return SpamClassificationContext(linked_channel=failed, profile_photo_age=failed)


def _get_recent_posts_limit() -> int:
    spam_cfg = load_config().get("spam", {})
    limit = spam_cfg.get("recent_posts_limit", 5)
    return limit if isinstance(limit, int) and limit > 0 else 5


async def _resolve_username_to_channel_id(username: str) -> int | None:
    """
    Resolve a Telegram username to a channel/supergroup chat ID via Bot API.
    Returns None if not a channel/supergroup or on error.
    """
    try:
        chat = await bot.get_chat(f"@{username}")
        chat_type = getattr(chat, "type", None)
        if chat_type in ("channel", "supergroup"):
            return chat.id
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Could not resolve username to channel",
            extra={"username": username, "error": str(exc)},
        )
    return None


def _parse_user_context_input(
    user_id_or_message: Any, username: str | None, chat_id: int | None
) -> tuple[Any, int | None, int | None, str | None]:
    if hasattr(user_id_or_message, "chat"):
        message_obj = user_id_or_message
        actual_user_id = (
            getattr(message_obj.from_user, "id", None)
            if message_obj.from_user
            else None
        )
        actual_chat_id = message_obj.chat.id
        resolved_username = (
            username
            if username is not None
            else (
                getattr(message_obj.from_user, "username", None)
                if message_obj.from_user
                else None
            )
        )
        return message_obj, actual_user_id, actual_chat_id, resolved_username

    return None, user_id_or_message, chat_id, username


def _pick_first_linked_channel_mention(
    full_user: JsonDict, message: Any
) -> tuple[str | None, str | None]:
    if (about := full_user.get("about")) and (username_from_bio := extract_first_channel_mention(about)):
        return username_from_bio, "bio"

    if message is not None:
        msg_text = (message.text or message.caption or "") or ""
        entities = getattr(message, "entities", None)
        if username_from_message := extract_first_channel_mention(msg_text, entities):
            return username_from_message, "message"

    return None, None


def _resolve_user_identifier(
    *, actual_user_id: int | None, username: str | None
) -> str | int | None:
    return username or actual_user_id


async def _fetch_full_user_and_account_context(
    *,
    client: MtprotoHttpClient,
    identifier: str | int,
    actual_user_id: int | None,
    username: str | None,
) -> tuple[JsonDict, JsonDict, ContextResult, bool | None]:
    """Fetch full user via MTProto. Returns (full_user, full_user_response, account_info_result, is_premium)."""
    full_user: JsonDict = {}
    full_user_response: JsonDict = {}
    account_info_result = _empty_context_result()
    is_premium: bool | None = None
    try:
        full_user_response = await client.call(
            "users.getFullUser",
            params={"id": identifier},
            resolve=True,
        )
        full_user = full_user_response.get("full_user") or {}
        if user_id := full_user.get("id") or actual_user_id:
            profile_photo = full_user.get("profile_photo")
            photo_date = None
            if profile_photo and isinstance(profile_photo, dict):
                photo_date = _extract_date(profile_photo.get("date"))
            account_info_result = ContextResult(
                status=ContextStatus.FOUND,
                content=UserAccountInfo(
                    user_id=int(user_id),
                    profile_photo_date=photo_date,
                ),
            )
        is_premium = _extract_is_premium(full_user)
    except MtprotoHttpError as exc:
        logger.info(
            "MTProto failed for full user",
            extra={
                "user_id": actual_user_id,
                "username": username,
                "identifier_used": identifier,
                "error": str(exc),
            },
        )
        account_info_result = ContextResult(status=ContextStatus.FAILED, error=str(exc))

    return full_user, full_user_response, account_info_result, is_premium


async def _collect_linked_channel_from_profile(
    *,
    full_user: JsonDict,
    full_user_response: JsonDict,
    actual_user_id: int | None,
) -> ContextResult | None:
    if not (personal_channel_id := full_user.get("personal_channel_id")):
        return None

    channel_id = int(personal_channel_id)
    linked_username = _extract_personal_channel_username(full_user_response, channel_id)
    if linked_username is None:
        return _empty_context_result()

    return await collect_channel_summary_by_id(
        channel_id,
        actual_user_id,
        username=linked_username,
        channel_source="linked",
    )


async def _collect_linked_channel_from_mentions(
    *,
    full_user: JsonDict,
    message: Any,
    actual_user_id: int | None,
) -> ContextResult:
    candidate_username, source = _pick_first_linked_channel_mention(full_user, message)
    if candidate_username and source:
        channel_id = await _resolve_username_to_channel_id(candidate_username)
        if channel_id is not None:
            return await collect_channel_summary_by_id(
                channel_id,
                actual_user_id,
                username=candidate_username,
                channel_source=source,
            )
        logger.debug(
            "Resolved mention is not a channel",
            extra={
                "user_id": actual_user_id,
                "candidate": candidate_username,
                "source": source,
            },
        )
        return _empty_context_result()

    logger.debug(
        "User has no linked channel in profile, bio, or message",
        extra={"user_id": actual_user_id, "full_user_keys": list(full_user.keys())},
    )
    return _empty_context_result()


def _extract_personal_channel_username(
    full_user_response: dict, personal_channel_id: int
) -> str | None:
    """
    Extract username for personal channel from MTProto users.getFullUser chats array.
    The response includes the channel in chats with id and username—no Bot API needed.
    """
    chats = full_user_response.get("chats") or []
    for chat in chats:
        if chat.get("id") == personal_channel_id:
            if username := chat.get("username"):
                return username
            # Collectible usernames: usernames is Vector<Username>
            usernames = chat.get("usernames") or []
            return next(
                (
                    u["username"]
                    for u in usernames
                    if isinstance(u, dict) and u.get("active") and u.get("username")
                ),
                None,
            )
    return None


async def collect_user_context(
    user_id_or_message,
    username: str | None = None,
    chat_id: int | None = None,
) -> SpamClassificationContext:
    """
    Collects user context including linked channel summary and account age signals.

    Args:
        user_id_or_message: Either a user_id (int) or a Telegram message object
        username: Optional username for the user (ignored when message object is passed)
        chat_id: Optional chat ID (ignored when message object is passed)

    Returns:
        SpamClassificationContext with linked_channel, profile_photo_age; stories=SKIPPED
    """
    client = get_mtproto_client()
    message, actual_user_id, actual_chat_id, username = _parse_user_context_input(
        user_id_or_message, username, chat_id
    )

    with logfire.span(
        "Collecting user context via MTProto",
        user_id=actual_user_id,
        username=username,
        chat_id=actual_chat_id,
    ):
        identifier = _resolve_user_identifier(
            actual_user_id=actual_user_id, username=username
        )
        if identifier is None:
            logger.error(
                "No username and invalid user_id for user_id-based collection",
                extra={"user_id": actual_user_id, "username": username},
            )
            return _failed_spam_context("Invalid user_id for user_id-based collection")

        (
            full_user,
            full_user_response,
            account_info_result,
            is_premium,
        ) = await _fetch_full_user_and_account_context(
            client=client,
            identifier=identifier,
            actual_user_id=actual_user_id,
            username=username,
        )

        linked_channel_result = await _collect_linked_channel_from_profile(
            full_user=full_user,
            full_user_response=full_user_response,
            actual_user_id=actual_user_id,
        )
        if linked_channel_result is None:
            linked_channel_result = await _collect_linked_channel_from_mentions(
                full_user=full_user,
                message=message,
                actual_user_id=actual_user_id,
            )

    return SpamClassificationContext(
        linked_channel=linked_channel_result,
        profile_photo_age=account_info_result,
        is_premium=is_premium,
    )


async def collect_channel_summary_by_id(
    channel_id: int,
    user_reference: str | int | None = "unknown",
    username: str | None = None,
    channel_source: str | None = None,
) -> ContextResult[LinkedChannelSummary]:
    """
    Collects summary stats and user list for a specific channel ID.

    Retrieves channel statistics (subscribers, posts, age) and the complete list
    of channel users from channels.getFullChannel API call. User data is stored
    in the returned LinkedChannelSummary for use in notifications and analysis.
    """
    client = get_mtproto_client()

    # Get the appropriate MTProto identifier for the channel
    if username:
        channel_identifier = username
    else:
        channel_identifier = bot_api_chat_id_to_mtproto(channel_id)

    with logfire.span(
        "Fetching channel summary via MTProto",
        user_reference=user_reference,
        channel_id=channel_id,
        username=username,
    ):
        try:
            full_channel = await client.call(
                "channels.getFullChannel",
                params={"channel": channel_identifier},
                resolve=True,
            )
        except MtprotoHttpError as exc:
            logger.info(
                "Failed to load full channel via MTProto",
                extra={
                    "user_reference": user_reference,
                    "channel_id": channel_id,
                    "username": username,
                    "identifier_used": channel_identifier,
                    "error": str(exc),
                },
            )
            return ContextResult(status=ContextStatus.FAILED, error=str(exc))

    subscribers = full_channel.get("full_chat", {}).get("participants_count")
    logger.debug(
        "MTProto channel stats retrieved",
        extra={
            "user_reference": user_reference,
            "channel_id": channel_id,
            "subscribers": subscribers,
        },
    )

    # Use the identifier that worked for the history call to ensure consistency
    peer_to_use = channel_identifier

    # Fetch recent posts content for spam analysis - this also gives us the newest message and total count
    recent_posts_limit = _get_recent_posts_limit()

    (
        recent_posts_content,
        newest_message,
        oldest_message_in_recent_batch,
        total_posts,
    ) = await _fetch_recent_posts_content(client, peer_to_use, limit=recent_posts_limit)

    newest_post_date = _extract_message_date(newest_message)
    oldest_post_date = None
    if total_posts and total_posts > 1:
        if total_posts <= recent_posts_limit:
            oldest_post_date = _extract_message_date(oldest_message_in_recent_batch)
        else:
            oldest_message, _ = await _fetch_channel_edge_message(
                client, peer_to_use, limit_offset=total_posts - 1
            )
            oldest_post_date = _extract_message_date(oldest_message)

    oldest_post_date = oldest_post_date or newest_post_date
    post_age_delta = None
    if newest_post_date and oldest_post_date:
        delta_days = (newest_post_date - oldest_post_date).days
        post_age_delta = max(delta_days // 30, 0)

    # Extract users list for channel admin notifications
    users = full_channel.get("users", [])

    summary = LinkedChannelSummary(
        subscribers=subscribers,
        total_posts=total_posts,
        post_age_delta=post_age_delta,
        recent_posts_content=recent_posts_content or None,
        users=users,
        channel_source=channel_source,
        channel_id=channel_id,
    )

    logger.info(
        "Channel summary collected via MTProto",
        extra={
            "source": "mtproto",
            "user_reference": user_reference,
            "channel_id": channel_id,
            "subscribers": subscribers,
            "total_posts": total_posts,
            "post_age_delta": post_age_delta,
            "users_count": len(users) if users else 0,
        },
    )

    return ContextResult(status=ContextStatus.FOUND, content=summary)


async def _fetch_channel_edge_message(
    client: MtprotoHttpClient,
    peer_reference: int | str,
    *,
    limit_offset: int | None,
) -> tuple[JsonDict | None, int | None]:
    params = _build_history_params(
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

    return _extract_first_message_and_total(history)


async def _fetch_recent_posts_content(
    client: MtprotoHttpClient,
    peer_reference: int | str,
    limit: int = 5,
) -> tuple[list[str], JsonDict | None, JsonDict | None, int | None]:
    """
    Fetch content from recent posts in a channel to analyze for spam indicators.
    Returns tuple of (content_list, newest_message, oldest_message_in_batch, total_count).
    - content_list: list of text content from recent posts (excluding media-only posts)
    - newest_message: the most recent message (first in results)
    - oldest_message_in_batch: oldest message present in the fetched batch
    - total_count: total number of messages in the channel
    """
    params = _build_history_params(
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
    content_list = []
    newest_message = messages[0] if messages else None
    oldest_message_in_batch = messages[-1] if len(messages) > 1 else newest_message
    total_count = history.get("count")

    for message in messages:
        # Extract text content from message
        text_content = _extract_message_text(message)
        if text_content and text_content.strip():
            content_list.append(text_content.strip())

    return content_list, newest_message, oldest_message_in_batch, total_count


def _extract_message_text(message: dict[str, Any]) -> str:
    """Extract text content from a Telegram message."""
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


def _extract_date(timestamp: Any) -> datetime | None:
    if not timestamp:
        return None
    if isinstance(timestamp, int):
        return datetime.fromtimestamp(timestamp, tz=UTC)
    if isinstance(timestamp, str):
        try:
            # Strings returned by the bridge are ISO8601 with timezone
            return datetime.fromisoformat(timestamp)
        except ValueError:
            logger.debug(
                "Failed to parse date from timestamp", extra={"timestamp": timestamp}
            )
            return None
    return None


def _extract_message_date(message: dict[str, Any] | None) -> datetime | None:
    return _extract_date(message.get("date")) if message else None


def _build_history_params(
    *, peer_reference: int | str, add_offset: int, limit: int
) -> JsonDict:
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


def _extract_first_message_and_total(
    history: JsonDict,
) -> tuple[JsonDict | None, int | None]:
    messages = history.get("messages", [])
    first_message = messages[0] if messages else None
    total = history.get("count")
    if total is None and messages:
        total = len(messages)
    return first_message, total
