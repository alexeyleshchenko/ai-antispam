"""Message validation and early-exit checks for moderation pipeline."""

import logging

from aiogram import types

from ...common.bot import bot
from ...common.utils import format_chat_log
from ...database import get_group
from ...database.group_operations import is_trusted_member
from ...database.models import Group

logger = logging.getLogger(__name__)


async def validate_group_and_check_early_exits(
    chat_id: int, user_id: int, title: str | None = None, username: str | None = None
) -> tuple[Group | None, str]:
    """
    Validate group exists and check for early exit conditions.

    Performs validation that group exists and moderation is enabled, then checks
    for conditions that should cause early exit from message processing (admin
    messages, approved users).

    Args:
        chat_id: The chat ID to validate
        user_id: The user ID to check
        title: Optional group title for log enrichment (no re-fetch)
        username: Optional group username for log enrichment (no re-fetch)

    Returns:
        Tuple of (group, exit_reason). If exit_reason is non-empty, processing
        should stop with the given reason. If exit_reason is empty string,
        processing should continue with the returned group.
    """
    group, group_error = await get_and_check_group(chat_id, title, username)
    if group_error:
        return None, group_error

    # At this point group is guaranteed to be not None
    assert group is not None

    # Check if sender is an admin - skip immediately
    if user_id in group.admin_ids:
        return group, "message_from_admin_skipped"

    # Trusted members (probation complete) skip full pipeline
    if await is_trusted_member(chat_id, user_id):
        return group, "message_trusted_member_skipped"

    return group, ""


async def check_skip_channel_bot_message(message: types.Message) -> tuple[bool, str]:
    """
    Check if message from channel bot should be skipped in discussion groups.

    Channel messages posted by the channel itself (not users) should be skipped
    in discussion groups to avoid moderation of official channel posts.

    Args:
        message: The message to check

    Returns:
        Tuple of (should_skip, reason) where reason is empty if not skipped
    """
    if not message.sender_chat:
        return False, ""

    # Check if admin is posting as group (anonymous admin)
    if is_admin_posting_as_group(message):
        logger.debug(
            f"Skip moderation for message {message.message_id} "
            f"from admin posting as group {message.sender_chat.id} "
            f"in chat {message.chat.id}"
        )
        return True, "message_from_group_admin_skipped"

    # Get initial linked chat ID
    linked_chat_id = getattr(message.chat, "linked_chat_id", None)

    # Check if it's already a channel bot message
    if is_channel_bot_in_discussion(message, linked_chat_id):
        logger.debug(
            f"Skip moderation for message {message.message_id} "
            f"from channel bot {message.sender_chat.id} "
            f"in discussion group {message.chat.id}"
        )
        return True, "message_from_channel_bot_skipped"

    # Attempt API fetch if needed
    if should_attempt_api_fetch(message, linked_chat_id):
        linked_chat_id = await fetch_linked_chat_id(
            message.chat.id,
            getattr(message.chat, "title", None),
            getattr(message.chat, "username", None),
        )
        logger.debug(
            f"Fetched linked_chat_id via API: {format_chat_log(linked_chat_id) if linked_chat_id is not None else 'None'}"
        )

        if is_channel_bot_in_discussion(message, linked_chat_id):
            logger.debug(
                f"Skip moderation for message {message.message_id} "
                f"from channel bot {message.sender_chat.id} "
                f"in discussion group {message.chat.id} (with API fallback)"
            )
            return True, "message_from_channel_bot_skipped"

    return False, ""


def is_admin_posting_as_group(message: types.Message) -> bool:
    """
    Check if message is from admin posting as group (anonymous admin).

    Args:
        message: The message to check

    Returns:
        True if admin is posting as group, False otherwise
    """
    return message.sender_chat is not None and message.sender_chat.id == message.chat.id


async def fetch_linked_chat_id(
    chat_id: int, title: str | None = None, username: str | None = None
) -> int | None:
    """
    Fetch linked chat ID for a supergroup via Telegram API.

    Args:
        chat_id: The chat ID to fetch linked chat for
        title: Optional chat title for log enrichment
        username: Optional chat username for log enrichment

    Returns:
        Linked chat ID if found, None otherwise
    """
    try:
        chat_info = await bot.get_chat(chat_id)
        return getattr(chat_info, "linked_chat_id", None)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"Failed to fetch linked_chat_id via API for {format_chat_log(chat_id, title, username)}: {e}"
        )
        return None


def is_channel_bot_in_discussion(
    message: types.Message, linked_chat_id: int | None
) -> bool:
    """
    Check if channel bot is posting in its discussion group.

    Args:
        message: The message to check
        linked_chat_id: The linked chat ID of the discussion group

    Returns:
        True if channel bot is posting in discussion group, False otherwise
    """
    return (
        linked_chat_id is not None
        and message.sender_chat is not None
        and message.sender_chat.id == linked_chat_id
    )


def should_attempt_api_fetch(
    message: types.Message, linked_chat_id: int | None
) -> bool:
    """
    Determine if we should attempt to fetch linked_chat_id via API.

    Args:
        message: The message to check
        linked_chat_id: Current linked chat ID (if any)

    Returns:
        True if API fetch should be attempted, False otherwise
    """
    return (
        linked_chat_id is None
        and getattr(message.chat, "type", None) == "supergroup"
        and getattr(message.sender_chat, "type", None) == "channel"
    )


async def get_and_check_group(
    chat_id: int, title: str | None = None, username: str | None = None
) -> tuple[Group | None, str]:
    """
    Get group and check if moderation is enabled.

    Args:
        chat_id: The chat ID to look up
        title: Optional group title for log enrichment (no re-fetch)
        username: Optional group username for log enrichment (no re-fetch)

    Returns:
        Tuple of (group, error_reason). Returns (None, error_message) if group
        doesn't exist or moderation is disabled. Returns (group, "") if valid.
    """
    group = await get_group(chat_id)

    if not group:
        logger.info(
            f"Group not found for chat {format_chat_log(chat_id, title, username)}"
        )
        return None, "error_message_group_not_found"

    if not group.moderation_enabled:
        return None, "message_moderation_disabled"

    return group, ""
