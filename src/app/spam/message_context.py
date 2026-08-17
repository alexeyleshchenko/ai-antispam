"""
Message context collection and spam analysis.

This module handles the extraction of context information from messages
and performs spam analysis using the collected context.
"""

import logging

from aiogram import types

from ..common.utils import format_chat_or_channel_display
from ..types import ContextStatus, MessageContextResult
from .context_collector import collect_sender_context

logger = logging.getLogger(__name__)


async def collect_message_context(
    message: types.Message,
) -> MessageContextResult:
    """
    Collect all relevant context data for message analysis.

    This function focuses solely on data collection and does not perform
    the actual spam classification.

    Args:
        message: The Telegram message to analyze

    Returns:
        MessageContextResult containing all context data
    """
    message_text, is_story = extract_message_with_forward_context(message)

    reply_context = extract_reply_context(message)
    sender_context = await collect_sender_context(message)

    sender_context.reply = reply_context

    channel_users = None
    if sender_context.is_channel_sender:
        linked_channel_found = True
        if sender_context.linked_channel and sender_context.linked_channel.content:
            channel_users = sender_context.linked_channel.content.users
    else:
        linked_channel_found = (
            sender_context.linked_channel is not None
            and sender_context.linked_channel.status == ContextStatus.FOUND
        )
    return MessageContextResult(
        message_text,
        is_story,
        sender_context,
        linked_channel_found,
        channel_users,
    )


def extract_reply_context(message: types.Message) -> str | None:
    """
    Extract context from the message being replied to.

    Handles two types of reply context:
    1. Same-chat reply via reply_to_message (existing)
    2. Cross-chat reply via external_reply + quote (new — reply to an external source)

    Args:
        message: The message that might be a reply

    Returns:
        Reply context text if this is a reply, None otherwise
    """
    # Same-chat reply (existing behavior)
    if message.reply_to_message:
        return (
            message.reply_to_message.text
            or message.reply_to_message.caption
            or "[MEDIA_MESSAGE]"
        )

    # Cross-chat reply — reply to a message from another channel/chat
    if message.external_reply:
        source = _format_external_source(message.external_reply.origin)
        quoted = (
            message.quote.text[:2000].strip()
            if message.quote and message.quote.text
            else None
        )
        logger.debug(
            "Cross-channel reply detected: origin_type=%s, source=%s, quoted_len=%s",
            message.external_reply.origin.type
            if message.external_reply.origin
            else None,
            source,
            len(quoted) if quoted else 0,
        )
        if quoted:
            return f'[REPLY TO EXTERNAL SOURCE from "{source}"]: {quoted}'
        return f"[REPLY TO EXTERNAL SOURCE from {source}]"

    return None


def _format_external_source(origin) -> str:
    """Format a human-readable source description from a MessageOrigin object.

    Returns channel title (preferred), possibly with @username, or a fallback.
    Title is more informative than username for spam classification context.
    """
    if origin is None:
        return "unknown source"

    if origin.type == "channel":
        if chat := origin.chat:
            parts = []
            if chat.title:
                parts.append(chat.title)
            if chat.username:
                parts.append(f"@{chat.username}")
            return " / ".join(parts) if parts else "channel"
        return "channel"
    elif origin.type == "chat":
        if chat := origin.sender_chat:
            if chat.title:
                return chat.title
            return f"@{chat.username}" if chat.username else "group"
        return "group"
    elif origin.type == "hidden_user":
        return origin.sender_user_name or "hidden user"

    elif origin.type == "user":
        if user := origin.sender_user:
            return f"@{user.username}" if user.username else user.full_name
        return "user"

    logger.warning("Unknown MessageOrigin type: %s", origin.type)
    return "unknown source"


def _extract_message_text(message: types.Message) -> str:
    """Extract the text content from a message, handling different message types."""
    return message.text or message.caption or "[MEDIA_MESSAGE]"


def _collect_forward_info(message: types.Message) -> list[str]:
    """Collect information about forwarded content in the message."""
    forward_info = []

    if message.forward_from:
        forward_info.append(f"Forwarded from user: {message.forward_from.full_name}")

    if message.forward_from_chat:
        forward_info.append(f"Forwarded from chat: {message.forward_from_chat.title}")

    return forward_info


def _collect_story_info(message: types.Message) -> tuple[list[str], bool]:
    """Collect information about stories and determine if message is from a story."""
    story_info = []
    is_story = False

    if story_obj := getattr(message, "story", None):
        story_chat = getattr(getattr(story_obj, "chat", None), "title", "Unknown")
        story_username = getattr(getattr(story_obj, "chat", None), "username", "")
        story_info.append(f"Forwarded story from: {story_chat} (@{story_username})")
        is_story = True

    return story_info, is_story


def _collect_channel_info(message: types.Message) -> list[str]:
    """Collect information about channel senders."""
    channel_info = []

    if message.sender_chat and message.sender_chat.type == "channel":
        channel_title = message.sender_chat.title
        channel_username = getattr(message.sender_chat, "username", None)
        channel_info.append(
            f"Posted by channel: {format_chat_or_channel_display(channel_title, channel_username, 'Канал')}"
        )

    return channel_info


def _collect_inline_keyboard_urls(message: types.Message) -> list[str]:
    """Collect URLs from inline keyboard buttons in the message."""
    urls = []
    if reply_markup := getattr(message, "reply_markup", None):
        return (
            [
                button.url
                for row in inline_keyboard
                for button in row
                if getattr(button, "url", None)
            ]
            if (inline_keyboard := getattr(reply_markup, "inline_keyboard", None))
            else urls
        )
    else:
        return urls


def _collect_via_bot_info(message: types.Message) -> str | None:
    """Collect information about inline bot used to send the message."""
    via_bot = getattr(message, "via_bot", None)
    if not via_bot:
        return None

    bot_name = getattr(via_bot, "first_name", None) or "Unknown"
    bot_username = getattr(via_bot, "username", None)
    return f"Sent via bot: {bot_name}" + (f" (@{bot_username})" if bot_username else "")


def _combine_forward_info(message_text: str, forward_info: list[str]) -> str:
    """Combine message text with forward information if any exists."""
    if forward_info:
        return f"{message_text}\n[FORWARD_INFO]: {' | '.join(forward_info)}"
    return message_text


def _combine_inline_urls(message_text: str, urls: list[str]) -> str:
    """Append inline keyboard URLs to message text if any exist."""
    if urls:
        return f"{message_text}\n[INLINE_KEYBOARD_URLS]: {' | '.join(urls)}"
    return message_text


def _combine_via_bot(message_text: str, via_bot_info: str | None) -> str:
    """Append via_bot info to message text if present."""
    if via_bot_info:
        return f"{message_text}\n[VIA_BOT]: {via_bot_info}"
    return message_text


def extract_message_with_forward_context(message: types.Message) -> tuple[str, bool]:
    """
    Extract message text and forwarding context for spam analysis.

    Analyzes the message to extract text content, forwarding information, channel context,
    and story indicators that help with spam classification. Combines all context
    information into the message text for LLM processing.

    Args:
        message: The message to analyze

    Returns:
        Tuple of (message_text, is_story) where message_text includes forward info
    """
    message_text = _extract_message_text(message)

    # Collect different types of forward/channel information
    forward_info = _collect_forward_info(message)
    story_info, is_story = _collect_story_info(message)
    channel_info = _collect_channel_info(message)
    via_bot_info = _collect_via_bot_info(message)
    inline_urls = _collect_inline_keyboard_urls(message)

    # Combine all forward-related information
    all_forward_info = forward_info + story_info + channel_info

    # Add forward info to message text if any exists
    message_text = _combine_forward_info(message_text, all_forward_info)

    # Add inline keyboard URLs if any exist
    message_text = _combine_inline_urls(message_text, inline_urls)

    # Add via_bot info if present
    message_text = _combine_via_bot(message_text, via_bot_info)

    return message_text, is_story
