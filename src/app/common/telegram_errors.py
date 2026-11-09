"""Helpers for expected Telegram API errors during group lifecycle operations."""

import asyncpg
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
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
    "bot is not a member of the channel",
)

_PERMISSION_MESSAGE_MARKERS = (
    "not enough rights",
    "need administrator rights",
    "chat admin required",
    "can_delete_messages",
    "can_restrict_members",
    "message can't be deleted",
)

# Telegram refuses to ban/restrict a chat ADMIN: "user is an administrator of
# the chat". Deliberately NOT folded into `_PERMISSION_MESSAGE_MARKERS`, which
# means "the bot lacks rights" and drives the owner-facing permission
# notification — the two look alike (both are TelegramBadRequest refusals of the
# same call) and mean the opposite thing: here the bot HAS the rights and the
# target is simply immune. Conflating them told the owner the bot lacks rights
# when it had them, and buried the real signal (#130).
_TARGET_IS_ADMIN_MARKERS = ("user is an administrator of the chat",)

_MESSAGE_NOT_FOUND_MARKERS = (
    "message to delete not found",
    "message to edit not found",
    "message not found",
)

# A repeat tap on the same inline button is refused as a no-op edit. It is not a
# failure: the member is already looking at the requested page (#52).
_MESSAGE_NOT_MODIFIED_MARKERS = ("message is not modified",)

_BOT_TO_BOT_DISABLED_MARKERS = ("user_bot_to_bot_disabled",)


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


# The bot IS in the chat and the chat exists; it merely lacks the right to
# enumerate members. Deliberately NOT folded into `_INACCESSIBLE_MESSAGE_MARKERS`,
# whose markers do not contain this string and whose predicate drives RETIREMENT:
# a rights error must never retire a paying group (it is routed to the no-rights
# grace flow instead, which notifies the admins before anything is abandoned).
_MEMBER_LIST_INACCESSIBLE_MARKERS = ("member list is inaccessible",)

def is_member_list_inaccessible_error(error: Exception) -> bool:
    """True when Telegram refuses the admin list because the bot cannot read members.

    The group is alive and the bot is still in it; it has lost the member-list
    right (or was demoted). The correct response is to start the no-rights grace
    clock, never to retire the row — see `_MEMBER_LIST_INACCESSIBLE_MARKERS`.
    """
    if not isinstance(error, TelegramBadRequest):
        return False
    return _error_message_contains(error, _MEMBER_LIST_INACCESSIBLE_MARKERS)

def migrate_target_id(error: Exception) -> int | None:
    """The new supergroup id when Telegram reports an upgrade, else None.

    `TelegramMigrateToChat` is NOT a `TelegramBadRequest` — it subclasses
    `TelegramAPIError` directly — so `is_group_inaccessible_error` returns False
    for it and a guard written against `TelegramBadRequest` here would never
    match. That would make the migration branch dead code that reads as correct,
    and the upgraded group would then be reported as an unexplained failure
    forever.
    """
    if isinstance(error, TelegramMigrateToChat):
        return error.migrate_to_chat_id
    return None

def is_permission_error(error: Exception) -> bool:
    """True when the bot lacks admin rights for delete/ban (still in the group)."""
    if not isinstance(error, TelegramBadRequest):
        return False
    return _error_message_contains(error, _PERMISSION_MESSAGE_MARKERS)


def is_target_admin_error(error: Exception) -> bool:
    """True when Telegram refused an action because the TARGET is an admin.

    The bot has the rights; the target cannot be banned or restricted by anyone
    in this group. Not a failure and not a permission problem — the caller's
    correct response is to record the target as an admin and skip the rest of
    the moderation of that message (#130).
    """
    if not isinstance(error, TelegramBadRequest):
        return False
    return _error_message_contains(error, _TARGET_IS_ADMIN_MARKERS)

def is_message_not_found_error(error: Exception) -> bool:
    """True when the target message was already deleted or does not exist."""
    if isinstance(error, (TelegramBadRequest, TelegramNotFound)):
        return _error_message_contains(error, _MESSAGE_NOT_FOUND_MARKERS)
    return False


def is_message_not_modified_error(error: Exception) -> bool:
    """True when an edit was a no-op because the content already matched.

    A repeated tap on the same inline button lands here: Telegram refuses an
    edit that would change nothing. The member is already on the requested page,
    so this is expected rather than an error worth surfacing.
    """
    if not isinstance(error, TelegramBadRequest):
        return False
    return _error_message_contains(error, _MESSAGE_NOT_MODIFIED_MARKERS)


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


_BOT_KICKED_MARKERS = (
    "bot was kicked from the chat",
    "bot was kicked from the supergroup chat",
)


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
