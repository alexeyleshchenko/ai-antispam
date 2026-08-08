"""
Message handlers for Telegram bot moderation.

This module contains the main message handler registrations that delegate
to the specialized message processing modules.
"""

from aiogram import types

from .dp import dp
from .updates_filter import filter_handle_edited_message, filter_handle_message

# =============================================================================
# MAIN MESSAGE HANDLERS
# =============================================================================


@dp.message(filter_handle_message)
async def handle_moderated_message(message: types.Message) -> str:
    """
    Handle all messages in moderated groups.

    Delegates to the message pipeline module for processing.
    """
    # Import here to avoid circular imports
    from .message.pipeline import handle_moderated_message as pipeline_handler

    return await pipeline_handler(message)


@dp.edited_message(filter_handle_edited_message)
async def handle_moderated_edited_message(message: types.Message) -> str:
    """Re-moderate edited group messages (probation members and first-time posters)."""
    from .message.pipeline import handle_moderated_message as pipeline_handler

    return await pipeline_handler(message, source="edit")


@dp.channel_post()
async def handle_channel_post(message: types.Message) -> str:
    """
    Handle channel posts when bot is incorrectly added to a channel.

    Delegates to the channel management module for processing.
    """
    # Import here to avoid circular imports
    from .message.channel_management import handle_channel_post as channel_handler

    return await channel_handler(message)
