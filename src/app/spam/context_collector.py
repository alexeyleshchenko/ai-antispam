"""
Context collection orchestration and routing.

This module provides the main entry points for collecting spam analysis context
from different types of senders (users vs channels). It orchestrates the collection
process while delegating specific collection tasks to specialized modules.
"""

import asyncio
import contextlib
import logging
from typing import cast

import logfire

from ..common.bot import bot
from ..types import (
    ContextResult,
    ContextStatus,
    PeerResolutionContext,
    SpamClassificationContext,
)
from .stories import collect_user_stories
from .user_context_utils import establish_peer_resolution_context
from .user_profile import collect_channel_summary_by_id, collect_user_context

logger = logging.getLogger(__name__)


def _to_stories_context(
    result: object, user_id: int, username: str | None
) -> ContextResult[str]:
    """Convert gather result to ContextResult for stories."""
    if isinstance(result, Exception):
        logger.info(
            "Failed to collect user stories",
            extra={"user_id": user_id, "username": username, "error": str(result)},
        )
        return ContextResult(status=ContextStatus.FAILED, error=str(result))
    return cast("ContextResult[str]", result)


def _to_profile_context(
    result: object, user_id: int, username: str | None
) -> SpamClassificationContext:
    """Convert gather result to SpamClassificationContext from profile collection."""
    if isinstance(result, Exception):
        logger.info(
            "Failed to collect user profile context",
            extra={"user_id": user_id, "username": username, "error": str(result)},
        )
        failed = ContextResult(status=ContextStatus.FAILED, error=str(result))
        return SpamClassificationContext(
            linked_channel=failed, profile_photo_age=failed
        )
    return cast("SpamClassificationContext", result)


async def collect_user_context_with_stories(
    message,
    user_id: int,
    username: str | None = None,
) -> SpamClassificationContext:
    """
    Collect user context (profile and account info) and stories in parallel.

    Args:
        message: Telegram message object containing chat and thread information
        user_id: User ID for context collection
        username: Optional username for the user

    Returns:
        SpamClassificationContext with linked_channel, profile_photo_age, and stories
    """
    # Extract basic message info needed for stories collection
    chat_id = message.chat.id

    with logfire.span(
        "Collecting complete user context",
        user_id=user_id,
        username=username,
        chat_id=chat_id,
        message_thread_id=getattr(message, "message_thread_id", None),
        is_topic_message=getattr(message, "is_topic_message", False),
        thread_type="forum_topic"
        if getattr(message, "is_topic_message", False)
        else "discussion_thread"
        if getattr(message, "message_thread_id", None)
        else "none",
    ):
        # Establish peer resolution context when username is None
        if username is None and getattr(message, "message_id", None) is not None:
            peer_context = PeerResolutionContext.from_message(message, user_id)
            context_established = await establish_peer_resolution_context(peer_context)
        else:
            context_established = True  # No context establishment needed if we have username or missing params

        if not context_established:
            skipped = ContextResult(
                status=ContextStatus.SKIPPED,
                error="Peer resolution context establishment failed",
            )
            return SpamClassificationContext(
                linked_channel=skipped,
                profile_photo_age=skipped,
            )

        # Run stories and profile collection in parallel
        stories_task = collect_user_stories(user_id, username, chat_id)
        profile_task = collect_user_context(message, username=username)

        try:
            results = await asyncio.gather(
                stories_task, profile_task, return_exceptions=True
            )
            stories_context = _to_stories_context(results[0], user_id, username)
            profile_context = _to_profile_context(results[1], user_id, username)
            return SpamClassificationContext(
                linked_channel=profile_context.linked_channel,
                profile_photo_age=profile_context.profile_photo_age,
                stories=stories_context,
            )

        except Exception as exc:  # noqa: BLE001
            # Fallback in case gather itself fails
            logger.info(
                "Failed to collect stories and profile data in parallel",
                extra={
                    "user_id": user_id,
                    "username": username,
                    "error": str(exc),
                },
            )
            failed = ContextResult(status=ContextStatus.FAILED, error=str(exc))
            return SpamClassificationContext(
                linked_channel=failed,
                profile_photo_age=failed,
                stories=failed,
            )


async def _collect_channel_sender_context(
    message,
) -> SpamClassificationContext:
    """
    Collect context for messages sent on behalf of channels.

    Args:
        message: Telegram message from a channel sender

    Returns:
        SpamClassificationContext with name, bio, and linked_channel populated
    """
    sender_chat = message.sender_chat
    name = getattr(sender_chat, "title", "Channel")
    bio = getattr(message.chat, "description", None)
    username = getattr(sender_chat, "username", None)
    channel_id = sender_chat.id

    # For private channels without username, skip collection
    if not username:
        logger.debug(
            "Skipping context collection for private channel without username",
            extra={"channel_id": channel_id},
        )
        return SpamClassificationContext(
            name=name,
            bio=bio,
            linked_channel=ContextResult(
                status=ContextStatus.SKIPPED,
                error="Private channel without username",
            ),
            is_channel_sender=True,
        )

    # Collect channel context using existing function
    channel_result = await collect_channel_summary_by_id(
        channel_id, user_reference=channel_id, username=username
    )

    return SpamClassificationContext(
        name=name,
        bio=bio,
        linked_channel=channel_result,
        is_channel_sender=True,
    )


async def _collect_user_sender_context(message) -> SpamClassificationContext:
    """
    Collect context for messages sent by users.

    Args:
        message: Telegram message from a user sender

    Returns:
        SpamClassificationContext with stories, linked_channel, profile_photo_age, name, bio
    """
    from_user = message.from_user
    user_id = from_user.id
    username = getattr(from_user, "username", None)
    name = from_user.full_name if from_user else "Unknown"
    is_premium = getattr(from_user, "is_premium", None)

    bio = None
    with contextlib.suppress(Exception):
        user_with_bio = await bot.get_chat(user_id)
        bio = user_with_bio.bio if user_with_bio else None
    ctx = await collect_user_context_with_stories(
        message=message,
        user_id=user_id,
        username=username,
    )
    return SpamClassificationContext(
        name=name,
        bio=bio,
        linked_channel=ctx.linked_channel,
        stories=ctx.stories,
        profile_photo_age=ctx.profile_photo_age,
        is_premium=is_premium,
    )


async def collect_sender_context(message) -> SpamClassificationContext:
    """
    Collect context for spam classification based on message sender type.

    Args:
        message: Telegram message object

    Returns:
        SpamClassificationContext with is_channel_sender set accordingly
    """
    if message.sender_chat and message.sender_chat.id != message.chat.id:
        return await _collect_channel_sender_context(message)
    if message.from_user:
        return await _collect_user_sender_context(message)
    logger.warning("Message has no sender_chat or from_user")
    return SpamClassificationContext(name="Unknown")
