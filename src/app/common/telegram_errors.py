"""Helpers for expected Telegram API errors during group lifecycle operations."""

import asyncpg
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramServerError,
)

# Telegram's GroupAnonymousBot — appears in admin lists of groups
# with anonymous admin enabled, but cannot receive bot-to-bot DMs.
# Telegram reports is_bot=False for this system account, so the
# standard not admin.user.is_bot filter doesn't catch it.
GROUP_ANONYMOUS_BOT_ID = 1087968824

_INACCESSIBLE_MESSAGE_MARKERS = (
    "chat not found",
    "group chat was deleted",
    "bot was kicked",
    "bot is not a member of the group",
    "bot is not a member of the supergroup",
)

_PERMISSION_MESSAGE_MARKERS = (
    "not enough rights",
    "need administrator rights",
    "chat admin required",
    "can_delete_messages",
    "can_restrict_members",
    "message can't be deleted",
)

_MESSAGE_NOT_FOUND_MARKERS = (
    "message to delete not found",
    "message to edit not found",
    "message not found",
)

_BOT_TO_BOT_DISABLED_MARKERS = (
    "user_bot_to_bot_disabled",
)


def _error_message_contains(error: Exception, markers: tuple[str, ...]) -> bool:
    msg = str(error).lower()
    return any(marker in msg for marker in markers)


def is_group_inaccessible_error(error: Exception) -> bool:
    """True when the bot can no longer access the group (left, kicked, or deleted)."""
    if isinstance(
        error, (TelegramForbiddenError, TelegramBadRequest, TelegramNotFound)
    ):
        return _error_message_contains(error, _INACCESSIBLE_MESSAGE_MARKERS)
    return False


def is_permission_error(error: Exception) -> bool:
    """True when the bot lacks admin rights for delete/ban (still in the group)."""
    if not isinstance(error, TelegramBadRequest):
        return False
    return _error_message_contains(error, _PERMISSION_MESSAGE_MARKERS)


def is_message_not_found_error(error: Exception) -> bool:
    """True when the target message was already deleted or does not exist."""
    if isinstance(error, (TelegramBadRequest, TelegramNotFound)):
        return _error_message_contains(error, _MESSAGE_NOT_FOUND_MARKERS)
    return False


def is_bot_to_bot_disabled_error(error: Exception) -> bool:
    """True when sending a DM fails because the recipient is a bot that doesn't allow bot DMs.

    This happens with Telegram's GroupAnonymousBot (1087968824) which appears in admin lists
    of groups with anonymous admin enabled. It cannot receive bot-to-bot DMs.
    """
    if not isinstance(error, TelegramBadRequest):
        return False
    return _error_message_contains(error, _BOT_TO_BOT_DISABLED_MARKERS)


_USER_BLOCKED_MARKERS = ("bot was blocked by the user",)


def is_user_blocked_error(error: Exception) -> bool:
    """True when sending a DM fails because the user blocked the bot."""
    if not isinstance(error, TelegramForbiddenError):
        return False
    return _error_message_contains(error, _USER_BLOCKED_MARKERS)


_BOT_KICKED_MARKERS = ("bot was kicked from the chat", "bot was kicked from the supergroup chat")


def is_bot_kicked_error(error: Exception) -> bool:
    """True when the bot was kicked from the group (cannot delete messages after leaving)."""
    if not isinstance(error, TelegramForbiddenError):
        return False
    return _error_message_contains(error, _BOT_KICKED_MARKERS)


_WEBHOOK_RETRYABLE_TYPES = (
    TelegramNetworkError,
    TelegramServerError,
    OSError,
    ConnectionError,
    asyncpg.PostgresConnectionError,
    asyncpg.InterfaceError,
)


def is_webhook_retryable(error: Exception) -> bool:
    """True when main.py should return 503 so Telegram redelivers the update."""
    return isinstance(error, _WEBHOOK_RETRYABLE_TYPES)
