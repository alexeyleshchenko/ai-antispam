"""
MTProto utility functions for chat ID conversions and common operations.

This module contains shared utilities for MTProto API interactions,
particularly for converting between different chat ID formats.
"""

import logging

logger = logging.getLogger(__name__)


def bot_api_chat_id_to_mtproto(chat_id: int) -> int | str:
    """
    Convert a Bot API chat ID to MTProto format.

    Bot API uses negative IDs for channels/supergroups:
    - Channels: -100xxxxxxxxx (where xxxxxxxxxx is the MTProto ID)
    - Supergroups: -100xxxxxxxxx (where xxxxxxxxxx is the MTProto ID)
    - Groups: -xxxxxxxxx (where xxxxxxxxx is the MTProto ID)

    MTProto uses positive IDs without the -100 prefix for channels/supergroups.

    Args:
        chat_id: Bot API chat ID (can be negative for channels/supergroups)

    Returns:
        Union[int, str]: MTProto-compatible identifier
    """
    if chat_id >= 0:
        # Already a positive ID (user IDs, direct chats)
        return chat_id

    # Convert negative Bot API ID to positive MTProto ID
    str_id = str(chat_id)

    if str_id.startswith("-100"):
        # Channel or supergroup: remove -100 prefix
        return int(str_id[4:])
    elif str_id.startswith("-"):
        # Regular group: remove - prefix
        return int(str_id[1:])
    else:
        # Fallback - shouldn't happen with valid Bot API IDs
        logger.warning(
            "Unexpected chat ID format, returning as-is",
            extra={"chat_id": chat_id, "str_id": str_id},
        )
        return chat_id


def get_mtproto_chat_identifier(
    chat_id: int, username: str | None = None
) -> int | str:
    """
    Get the appropriate MTProto identifier for a chat, preferring username over ID.

    Args:
        chat_id: Bot API chat ID
        username: Optional username for the chat

    Returns:
        Union[int, str]: Username if available, otherwise MTProto-formatted chat ID
    """
    return username or bot_api_chat_id_to_mtproto(chat_id)
