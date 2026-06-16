"""
Utilities for user context collection and MTProto peer resolution.

This module provides functions for:
- User bot subscription management for public chats
- Establishing peer resolution context through various strategies
- Handling MTProto API interactions for context collection

All functions are designed to be imported without creating circular dependencies.
"""

import logging
from typing import Dict, Optional, Any

import logfire

from ..common.mtproto_client import MtprotoHttpError, get_mtproto_client
from ..common.mtproto_utils import get_mtproto_chat_identifier
from ..types import PeerResolutionContext

logger = logging.getLogger(__name__)


# =============================================================================
# Error Handling Utilities
# =============================================================================

logger = logging.getLogger(__name__)


# =============================================================================
# Error Handling Utilities
# =============================================================================


# Helper functions for error handling and logging
def _is_user_already_participant_error(error_msg: str) -> bool:
    """Check if error indicates user is already a participant.

    Checks for error patterns: 'user already participant' or 'already'.
    """
    return ERROR_USER_ALREADY_PARTICIPANT in error_msg or ERROR_ALREADY in error_msg


def _is_channel_private_error(error_msg: str) -> bool:
    """Check if error indicates channel is private.

    Checks for error patterns: 'channel private' or 'private'.
    """
    return ERROR_CHANNEL_PRIVATE in error_msg or ERROR_PRIVATE in error_msg


def _is_message_not_found_error(error_msg: str) -> bool:
    """Check if error indicates message was not found or deleted.

    Checks for error patterns: 'message not found' or 'message deleted'.
    """
    return ERROR_MESSAGE_NOT_FOUND in error_msg or ERROR_MESSAGE_DELETED in error_msg


def _is_msg_id_invalid_error(error_msg: str) -> bool:
    """Check if error indicates an invalid message ID (e.g. no discussion thread).

    Checks for error pattern: 'msg_id_invalid'.
    """
    return ERROR_MSG_ID_INVALID in error_msg


def _should_skip_join_for_error(error_msg: str) -> bool:
    """Determine if join should be skipped based on error type (Option 2B).

    Considers the following permanent errors that indicate join should be skipped:
    - Channel private errors
    - Invalid channel/chat/peer errors

    Args:
        error_msg: The error message to evaluate

    Returns:
        True if join should be skipped due to permanent error, False otherwise
    """
    error_lower = error_msg.lower()

    # Permanent errors - skip join
    skip_patterns = [
        ERROR_CHANNEL_PRIVATE,
        ERROR_PRIVATE,
        ERROR_USER_ALREADY_PARTICIPANT,  # This shouldn't happen in pre-check but included for completeness
        ERROR_CHANNEL_INVALID,
        ERROR_CHAT_ID_INVALID,
        ERROR_PEER_ID_INVALID,
    ]

    return any(pattern in error_lower for pattern in skip_patterns)


def _create_chat_context(
    chat_id: int,
    user_id: Optional[int] = None,
    message_id: Optional[int] = None,
    chat_username: Optional[str] = None,
    **extra_fields: Any,
) -> Dict[str, Any]:
    """Create standardized logging context for chat-related operations."""
    context: Dict[str, Any] = {"chat_id": chat_id}

    if user_id is not None:
        context["user_id"] = user_id
    if message_id is not None:
        context["message_id"] = message_id
    if chat_username is not None:
        context["chat_username"] = chat_username

    context |= extra_fields
    return context


def _log_mtproto_error(
    error: MtprotoHttpError,
    operation: str,
    context: Dict[str, Any],
    log_level: int = logging.WARNING,
) -> None:
    """Standardized logging for MTProto errors."""
    logger.log(
        log_level,
        f"{operation} failed",
        extra={**context, "error": str(error)},
        exc_info=True,
    )


def _log_unexpected_error(
    error: Exception, operation: str, context: Dict[str, Any]
) -> None:
    """Standardized logging for unexpected errors."""
    logger.error(
        f"Unexpected error during {operation}",
        extra={**context, "error": str(error)},
        exc_info=True,
    )


# =============================================================================
# MTProto API Constants
# =============================================================================

# Constants for MTProto API calls
DEFAULT_MESSAGE_LIMIT = 1
THREAD_MESSAGE_LIMIT = 10
HISTORY_OFFSET_INCREMENT = 1
DEFAULT_OFFSET = 0
DEFAULT_HASH = 0

# Error message patterns for MTProto errors
ERROR_USER_ALREADY_PARTICIPANT = "user already participant"
ERROR_ALREADY = "already"
ERROR_CHANNEL_PRIVATE = "channel private"
ERROR_PRIVATE = "private"
ERROR_MESSAGE_NOT_FOUND = "message not found"
ERROR_MESSAGE_DELETED = "message deleted"
ERROR_CHANNEL_INVALID = "channel invalid"
ERROR_CHAT_ID_INVALID = "chat id invalid"
ERROR_MSG_ID_INVALID = "msg_id_invalid"
ERROR_PEER_ID_INVALID = "peer id invalid"

# Context source types
CONTEXT_SOURCE_DISCUSSION_GROUP = "discussion_group"
CONTEXT_SOURCE_MAIN_CHANNEL = "main_channel"


# =============================================================================
# Constants and Configuration
# =============================================================================


# =============================================================================
# Utility Functions
# =============================================================================


def determine_thread_type(
    message_thread_id: Optional[int] = None,
    is_topic_message: bool = False,
) -> str:
    """
    Determine the type of thread based on message properties.

    Args:
        message_thread_id: Thread ID if present
        is_topic_message: Whether this is a forum topic message

    Returns:
        Thread type string: "forum_topic", "discussion_thread", or "none"
    """
    if is_topic_message:
        return "forum_topic"
    elif message_thread_id:
        return "discussion_thread"
    else:
        return "none"


# =============================================================================
# User Bot Subscription Management
# =============================================================================


# =============================================================================
# Context Establishment Functions
# =============================================================================


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def attempt_user_bot_chat_join(
    chat_id: int, chat_username: Optional[str] = None
) -> bool:
    """
    Attempt to join the user bot to a public chat (group/supergroup) using MTProto channels.joinChannel.

    This function tries to join a public group or supergroup using its username.
    It cannot work with private chats that don't have usernames.

    Args:
        chat_id: Bot API chat ID (can be negative for groups/supergroups)
        chat_username: Optional username of the chat for joining

    Returns:
        bool: True if join succeeds or bot is already a member, False otherwise

    Raises:
        No exceptions are raised - all errors are handled internally and logged.

    Note:
        Cannot join chats without usernames (private groups).
        Already being a participant is treated as success.
        Used for establishing peer resolution context in groups where users send messages.
    """
    client = get_mtproto_client()

    # Cannot subscribe to chats without usernames (private channels)
    if not chat_username:
        logger.debug(
            "Skipping subscription to chat without username (likely private)",
            extra=_create_chat_context(chat_id, chat_username=chat_username),
        )
        return False

    context = _create_chat_context(chat_id, chat_username=chat_username)

    with logfire.span(
        "Joining user bot to chat", chat_id=chat_id, chat_username=chat_username
    ):
        try:
            logger.debug(
                f"Attempting to join user bot to chat with identifier: {chat_username}"
            )

            await client.call(
                "channels.joinChannel", params={"channel": chat_username}, resolve=True
            )

            logger.info(
                "Successfully joined user bot to chat",
                extra={**context, "identifier_used": chat_username},
            )
            return True

        except MtprotoHttpError as e:
            error_msg = str(e).lower()

            # Already joined - treat as success
            if _is_user_already_participant_error(error_msg):
                logger.debug("User bot already joined to chat", extra=context)
                return True

            # Private chat - can't join
            if _is_channel_private_error(error_msg):
                logger.info("Cannot join user bot to private chat", extra=context)
                return False

            # Other MTProto errors - log warning and return False
            _log_mtproto_error(e, "user bot chat join", context)
            return False

        except Exception as e:
            _log_unexpected_error(e, "user bot chat join", context)
            return False


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def establish_context_via_group_reading(
    context: PeerResolutionContext,
) -> bool:
    """
    Establish peer resolution context by reading a specific message from a group chat.

    This function attempts to read a single message from the chat to establish MTProto
    peer resolution context. This is necessary for the user bot to resolve user IDs
    to usernames/peer information when collecting spam context.

    Args:
        context: PeerResolutionContext object containing all resolution parameters

    Returns:
        bool: True if context was established successfully, False otherwise

    Note:
        Message not found/deleted errors are treated as acceptable and return True,
        as they don't prevent general context collection for the chat.
    """
    client = get_mtproto_client()
    chat_identifier = get_mtproto_chat_identifier(
        context.chat_id, context.chat_username
    )
    logging_context = _create_chat_context(
        context.chat_id,
        context.user_id,
        context.message_id,
        context.chat_username,
        chat_identifier=chat_identifier,
    )

    try:
        logger.debug(
            "Attempting group message reading for context establishment",
            extra=logging_context,
        )

        # Read the specific message to establish chat context
        # offset_id is exclusive, so +1 to include our target message
        message_result = await client.call(
            "messages.getHistory",
            params={
                "peer": chat_identifier,
                "offset_id": context.message_id + HISTORY_OFFSET_INCREMENT,
                "offset_date": DEFAULT_OFFSET,
                "add_offset": DEFAULT_OFFSET,
                "limit": DEFAULT_MESSAGE_LIMIT,
                "max_id": DEFAULT_OFFSET,
                "min_id": DEFAULT_OFFSET,
                "hash": DEFAULT_HASH,
            },
            resolve=True,
        )

        messages_found = len(message_result.get("messages", []))
        logger.debug(
            "Group message reading succeeded for context establishment",
            extra={**logging_context, "messages_found": messages_found},
        )

        return True

    except MtprotoHttpError as e:
        error_msg = str(e).lower()

        # Message not found/deleted is acceptable for context collection
        if _is_message_not_found_error(error_msg):
            logger.debug(
                "Message not found or deleted during group reading, but proceeding with context collection",
                extra=logging_context,
            )
            return True

        _log_mtproto_error(e, "group message reading", logging_context)
        return False

    except Exception as e:
        _log_unexpected_error(e, "group message reading", logging_context)
        return False


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def establish_context_via_thread_reading(
    context: PeerResolutionContext,
) -> bool:
    """
    Establish peer resolution context by reading messages from a discussion thread.

    When the thread is linked to a channel post, prefer ``messages.getReplies`` on the
    **main channel** peer (using ``main_channel_username`` / ``main_channel_id``).
    That matches Telegram's comment thread model and avoids resolving a **private**
    discussion supergroup by bare numeric id (often fails in MTProto).

    Fallback: if the main channel is unknown, use the discussion group chat and
    ``message_thread_id`` / ``reply_to_message_id`` as before. Channel-post
    ``msg_id`` prefers ``original_channel_post_id`` (``forward_from_message_id``),
    then thread / reply ids.

    Args:
        context: PeerResolutionContext object containing all resolution parameters

    Returns:
        bool: True if context was established successfully, False otherwise
    """
    client = get_mtproto_client()

    use_main_channel_peer = (
        context.main_channel_username is not None or context.main_channel_id is not None
    )

    if use_main_channel_peer:
        channel_chat_id = (
            context.main_channel_id
            if context.main_channel_id is not None
            else context.chat_id
        )
        chat_identifier = get_mtproto_chat_identifier(
            channel_chat_id, context.main_channel_username
        )
        target_message_id = (
            context.original_channel_post_id
            or context.message_thread_id
            or context.reply_to_message_id
        )
        context_source = CONTEXT_SOURCE_MAIN_CHANNEL
        target_chat_id = channel_chat_id
        target_chat_username = context.main_channel_username
    else:
        target_chat_id = context.chat_id
        target_chat_username = context.chat_username
        target_message_id = context.message_thread_id or context.reply_to_message_id
        context_source = CONTEXT_SOURCE_DISCUSSION_GROUP
        chat_identifier = get_mtproto_chat_identifier(
            target_chat_id, target_chat_username
        )

    logging_context = _create_chat_context(
        context.chat_id,
        context.user_id,
        context.message_id,
        context.chat_username,
        message_thread_id=context.message_thread_id,
        main_channel_id=context.main_channel_id,
        main_channel_username=context.main_channel_username,
        original_channel_post_id=context.original_channel_post_id,
        target_chat_id=target_chat_id,
        target_chat_username=target_chat_username,
        target_message_id=target_message_id,
        context_source=context_source,
        chat_identifier=chat_identifier,
    )

    try:
        logger.debug(
            "Attempting thread-based context establishment", extra=logging_context
        )

        # Read recent messages in the thread to establish peer context
        # This includes messages from various users, helping with peer resolution
        thread_result = await client.call(
            "messages.getReplies",
            params={
                "peer": chat_identifier,
                "msg_id": target_message_id,
                "offset_id": DEFAULT_OFFSET,
                "offset_date": DEFAULT_OFFSET,
                "add_offset": DEFAULT_OFFSET,
                "limit": THREAD_MESSAGE_LIMIT,  # Read up to 10 recent messages in the thread
                "max_id": DEFAULT_OFFSET,
                "min_id": DEFAULT_OFFSET,
                "hash": DEFAULT_HASH,
            },
            resolve=True,
        )

        messages_found = len(thread_result.get("messages", []))
        logger.debug(
            "Thread reading succeeded for context establishment",
            extra={**logging_context, "messages_found": messages_found},
        )

        return True

    except MtprotoHttpError as e:
        error_msg = str(e).lower()

        # MSG_ID_INVALID: channel post has no discussion thread (replies=null).
        # This often happens with grouped media albums where only the first message
        # in the group has the comments thread. Try to resolve the anchor first.
        if use_main_channel_peer and _is_msg_id_invalid_error(error_msg):
            anchor_id = None
            if target_message_id is not None:
                anchor_id = await _resolve_grouped_album_anchor(
                    channel_chat_id, target_chat_username, target_message_id, logging_context
                )
            if anchor_id is not None:
                try:
                    logger.debug(
                        "Retrying GetReplies with grouped album anchor",
                        extra={**logging_context, "anchor_msg_id": anchor_id},
                    )
                    thread_result = await client.call(
                        "messages.getReplies",
                        params={
                            "peer": chat_identifier,
                            "msg_id": anchor_id,
                            "offset_id": DEFAULT_OFFSET,
                            "offset_date": DEFAULT_OFFSET,
                            "add_offset": DEFAULT_OFFSET,
                            "limit": THREAD_MESSAGE_LIMIT,
                            "max_id": DEFAULT_OFFSET,
                            "min_id": DEFAULT_OFFSET,
                            "hash": DEFAULT_HASH,
                        },
                        resolve=True,
                    )
                    messages_found = len(thread_result.get("messages", []))
                    logger.debug(
                        "Grouped album anchor GetReplies succeeded",
                        extra={**logging_context, "messages_found": messages_found},
                    )
                    return True
                except Exception:
                    logger.debug(
                        "Grouped album anchor GetReplies also failed, "
                        "falling back to discussion group",
                        extra=logging_context,
                    )

            # No grouped album found or anchor also failed — fall back to discussion group
            logger.debug(
                "GetReplies returned MSG_ID_INVALID, "
                "falling back to discussion group reading",
                extra=logging_context,
            )
            return await _fallback_discussion_group_reading(context, logging_context)

        _log_mtproto_error(e, "thread-based context establishment", logging_context)
        return False

    except Exception as e:
        _log_unexpected_error(e, "thread-based context establishment", logging_context)
        return False


async def _resolve_grouped_album_anchor(
    channel_chat_id: int,
    channel_username: Optional[str],
    msg_id: int,
    logging_context: Dict[str, Any],
) -> Optional[int]:
    """Find the first message in a grouped media album containing msg_id.

    When GetReplies fails with MSG_ID_INVALID on a channel post that's part of
    a grouped album, the comments thread is anchored to the FIRST message in
    the group (the one with replies != null). This function fetches nearby
    messages from the channel, finds all messages sharing the same grouped_id,
    and returns the lowest message ID — the anchor.

    Returns the anchor message ID, or None if:
    - The message is not part of a grouped album (grouped_id is null)
    - The message couldn't be fetched
    - The target message is already the first in the group
    """
    client = get_mtproto_client()
    channel_identifier = get_mtproto_chat_identifier(channel_chat_id, channel_username)

    try:
        # Fetch messages from msg_id backwards. Telegram limits albums to 10 items,
        # so 20 is generous enough to always capture the full group.
        result = await client.call(
            "messages.getHistory",
            params={
                "peer": channel_identifier,
                "offset_id": msg_id + HISTORY_OFFSET_INCREMENT,
                "offset_date": DEFAULT_OFFSET,
                "add_offset": DEFAULT_OFFSET,
                "limit": 20,
                "max_id": DEFAULT_OFFSET,
                "min_id": DEFAULT_OFFSET,
                "hash": DEFAULT_HASH,
            },
            resolve=True,
        )

        messages = result.get("messages", [])
        if not messages:
            return None

        target_grouped_id = next(
            (
                msg.get("grouped_id")
                for msg in messages
                if msg.get("id") == msg_id
            ),
            None,
        )
        if not target_grouped_id:
            return None

        # Find the first message in the group (lowest ID with matching grouped_id)
        anchor_msg_id = msg_id
        for msg in messages:
            if msg.get("grouped_id") == target_grouped_id:
                anchor_msg_id = min(anchor_msg_id, msg["id"])

        if anchor_msg_id == msg_id:
            # Target is already the first in the group — GetReplies failed for another reason
            return None

        logger.debug(
            "Resolved grouped album anchor",
            extra={
                **logging_context,
                "anchor_msg_id": anchor_msg_id,
                "grouped_id": target_grouped_id,
            },
        )
        return anchor_msg_id

    except Exception as e:
        logger.debug(
            "Failed to resolve grouped album anchor",
            extra={**logging_context, "error": str(e)},
        )
        return None


async def _fallback_discussion_group_reading(
    context: PeerResolutionContext,
    logging_context: Dict[str, Any],
) -> bool:
    """Fall back to reading the discussion group when GetReplies fails with MSG_ID_INVALID.

    When the channel post has no discussion thread (replies=null), messages.getReplies
    returns MSG_ID_INVALID. This fallback reads the discussion group directly via
    messages.getHistory to still establish peer resolution context.
    """
    client = get_mtproto_client()
    group_identifier = get_mtproto_chat_identifier(
        context.chat_id, context.chat_username
    )

    try:
        result = await client.call(
            "messages.getHistory",
            params={
                "peer": group_identifier,
                "offset_id": context.message_id + HISTORY_OFFSET_INCREMENT,
                "offset_date": DEFAULT_OFFSET,
                "add_offset": DEFAULT_OFFSET,
                "limit": THREAD_MESSAGE_LIMIT,
                "max_id": DEFAULT_OFFSET,
                "min_id": DEFAULT_OFFSET,
                "hash": DEFAULT_HASH,
            },
            resolve=True,
        )

        messages_found = len(result.get("messages", []))
        logger.debug(
            "Discussion group fallback reading succeeded",
            extra={**logging_context, "messages_found": messages_found},
        )
        return True

    except Exception as e:
        logger.debug(
            "Discussion group fallback reading also failed",
            extra={**logging_context, "error": str(e)},
        )
        return False


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def check_membership_via_message_read(
    context: PeerResolutionContext,
) -> tuple[bool, Optional[str]]:
    """
    Check if user bot has membership in a chat by attempting to read a message.

    This function performs a fast membership pre-check by trying to read a message
    from the chat. If successful, the bot is confirmed to be a member and context
    is already established.

    Args:
        context: PeerResolutionContext object containing all resolution parameters

    Returns:
        tuple[bool, Optional[str]]: (is_member, error_type)
        - is_member=True if message read succeeds (bot is member)
        - is_member=False, error_type=None if message not found/deleted (still member)
        - is_member=False, error_type=str if access failed (error message)

    Note:
        Success includes "message not found" errors, as they indicate chat access.
        Only access-denied errors indicate non-membership.
    """
    client = get_mtproto_client()
    chat_identifier = get_mtproto_chat_identifier(
        context.chat_id, context.chat_username
    )
    logging_context = _create_chat_context(
        context.chat_id,
        context.user_id,
        context.message_id,
        context.chat_username,
        chat_identifier=chat_identifier,
    )

    logger.debug(
        "Performing membership pre-check via message read",
        extra=logging_context,
    )

    try:
        # Use same parameters as establish_context_via_group_reading for consistency
        message_result = await client.call(
            "messages.getHistory",
            params={
                "peer": chat_identifier,
                "offset_id": context.message_id + HISTORY_OFFSET_INCREMENT,
                "offset_date": DEFAULT_OFFSET,
                "add_offset": DEFAULT_OFFSET,
                "limit": DEFAULT_MESSAGE_LIMIT,
                "max_id": DEFAULT_OFFSET,
                "min_id": DEFAULT_OFFSET,
                "hash": DEFAULT_HASH,
            },
            resolve=True,
        )

        messages_found = len(message_result.get("messages", []))
        logger.debug(
            "Membership pre-check succeeded - bot is member",
            extra={**logging_context, "messages_found": messages_found},
        )

        return True, None

    except MtprotoHttpError as e:
        error_msg = str(e).lower()

        # Message not found/deleted is acceptable - bot is still a member
        if _is_message_not_found_error(error_msg):
            logger.debug(
                "Message not found during membership pre-check, but bot has access",
                extra=logging_context,
            )
            return True, None

        # Other MTProto errors indicate access failure
        logger.debug(
            "Membership pre-check failed - bot is not a member",
            extra={**logging_context, "error_type": error_msg},
        )
        return False, error_msg

    except Exception as e:
        # Unexpected errors - treat as access failure but log
        error_msg = str(e).lower()
        _log_unexpected_error(e, "membership pre-check", logging_context)
        return False, error_msg


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def _establish_context_for_regular_group_message(
    context: PeerResolutionContext,
) -> bool:
    """
    Establish peer resolution context for regular group messages or forum topics.

    This function handles the complex logic for regular group messages:
    - Performs membership pre-check via message reading for public chats
    - Skips join if pre-check succeeds (context already established)
    - Attempts join only if pre-check fails with transient errors
    - Skips context collection for private chats (no username)

    Args:
        context: PeerResolutionContext object containing all resolution parameters

    Returns:
        bool: True if peer resolution context was established successfully, False otherwise
    """
    message_type = "forum_topic" if context.is_topic_message else "regular_group"

    # Handle private chats (no username) - assume getHistory failure, no join possible
    if not context.chat_username:
        logger.debug(
            f"{message_type} message in private chat (no username), skipping context collection",
            extra={"context": context},
        )
        return False

    # Public chat with username - perform membership pre-check
    logger.debug(
        f"{message_type} message, performing membership pre-check before join",
        extra={"context": context},
    )

    # Pre-check membership via message reading
    is_member, error_type = await check_membership_via_message_read(context)

    if is_member:
        # Pre-check succeeded - context already established, skip join
        logger.debug(
            "Membership pre-check succeeded, skipping join",
            extra={"context": context},
        )
        return True

    # Pre-check failed - determine if we should attempt join
    if error_type and _should_skip_join_for_error(error_type):
        # Permanent error - skip join and log
        logger.info(
            f"Membership pre-check failed with permanent error, skipping join: {error_type}",
            extra={"context": context, "error_type": error_type},
        )
        return False
    else:
        # Transient error - attempt join
        logger.debug(
            f"Membership pre-check failed with transient error, attempting join: {error_type}",
            extra={"context": context, "error_type": error_type},
        )

        join_success = await attempt_user_bot_chat_join(
            context.chat_id, context.chat_username
        )

        if not join_success:
            logger.warning(
                "User bot subscription failed after pre-check failure, skipping context collection",
                extra={"context": context},
            )
            return False

        # Join succeeded - establish context
        return await establish_context_via_group_reading(context)


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def establish_peer_resolution_context(
    context: PeerResolutionContext,
) -> bool:
    """
    Establish MTProto peer resolution context for user context collection.

    This function employs different strategies based on message type to establish peer resolution context,
    which is necessary for the user bot to resolve user IDs when usernames are not available.

    **Discussion Thread Messages** (replies to channel posts in discussion groups):
    - Uses ``messages.getReplies`` on the **main channel** when ``main_channel_id`` /
      ``main_channel_username`` are known (preferred); otherwise on the discussion group
    - No subscription attempt needed when the channel has a public username

    **Regular Group Messages** (standard group chats or forum topics):
    - Performs membership pre-check via message reading for public chats
    - Skips join if pre-check succeeds (context already established)
    - Attempts join only if pre-check fails with transient errors
    - Skips context collection for private chats (no username)

    Args:
        context: PeerResolutionContext object containing all resolution parameters

    Returns:
        bool: True if peer resolution context was established successfully, False otherwise

    Note:
        Context establishment is crucial for the user bot to resolve user IDs to peer information
        when collecting spam analysis context. Different chat types require different strategies.
    """
    if not context.message_thread_id or context.is_topic_message:
        # Regular group message or forum topic message
        return await _establish_context_for_regular_group_message(context)
    # Discussion thread detected (channel reply), using thread-based peer resolution
    logger.debug(
        "Discussion thread detected (channel reply), using thread-based peer resolution",
        extra={"context": context},
    )
    return await establish_context_via_thread_reading(context)