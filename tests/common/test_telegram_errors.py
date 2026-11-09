from unittest.mock import MagicMock

import asyncpg
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramServerError,
)

from src.app.common.telegram_errors import (
    is_bot_kicked_error,
    is_bot_to_bot_disabled_error,
    is_group_inaccessible_error,
    is_member_list_inaccessible_error,
    is_message_not_found_error,
    is_permission_error,
    is_user_blocked_error,
    is_webhook_retryable,
    migrate_target_id,
)


def test_is_group_inaccessible_error_chat_not_found():
    err = TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
    assert is_group_inaccessible_error(err) is True


def test_is_group_inaccessible_error_bot_kicked():
    err = TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: bot was kicked from the group chat"
    )
    assert is_group_inaccessible_error(err) is True


def test_is_group_inaccessible_error_unrelated_bad_request():
    err = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: can't parse entities"
    )
    assert is_group_inaccessible_error(err) is False


def test_is_group_inaccessible_error_unrelated_forbidden():
    err = TelegramForbiddenError(
        method=MagicMock(),
        message="Forbidden: bot can't initiate conversation with a user",
    )
    assert is_group_inaccessible_error(err) is False


def test_is_group_inaccessible_error_group_chat_deleted():
    err = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: group chat was deleted"
    )
    assert is_group_inaccessible_error(err) is True


def test_is_group_inaccessible_error_bot_not_member():
    err = TelegramForbiddenError(
        method=MagicMock(),
        message="Forbidden: bot is not a member of the group chat",
    )
    assert is_group_inaccessible_error(err) is True


def test_is_group_inaccessible_error_bot_not_member_channel():
    """A channel the bot is not a member of is inaccessible, not an anomaly.

    Regression for issue #3: get_admin_groups logged an ERROR traceback for a
    stale channel row instead of the intended INFO + stale-record cleanup.
    """
    err = TelegramForbiddenError(
        method=MagicMock(),
        message="Forbidden: bot is not a member of the channel chat",
    )
    assert is_group_inaccessible_error(err) is True


def test_is_group_inaccessible_error_non_telegram():
    assert is_group_inaccessible_error(RuntimeError("network")) is False


def test_is_group_inaccessible_error_not_found_with_marker():
    err = TelegramNotFound(method=MagicMock(), message="Not Found: chat not found")
    assert is_group_inaccessible_error(err) is True


def test_is_group_inaccessible_error_not_found_unrelated():
    err = TelegramNotFound(method=MagicMock(), message="Not Found: user not found")
    assert is_group_inaccessible_error(err) is False


def test_is_permission_error_delete_rights():
    err = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: not enough rights to delete"
    )
    assert is_permission_error(err) is True


def test_is_permission_error_unrelated_bad_request():
    err = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: can't parse entities"
    )
    assert is_permission_error(err) is False


def test_is_permission_error_forbidden_not_checked():
    err = TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: not enough rights"
    )
    assert is_permission_error(err) is False


def test_is_message_not_found_error_delete():
    err = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: message to delete not found"
    )
    assert is_message_not_found_error(err) is True


def test_is_message_not_found_error_unrelated():
    err = TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
    assert is_message_not_found_error(err) is False


def test_is_webhook_retryable_telegram_network():
    err = TelegramNetworkError(method=MagicMock(), message="Network error")
    assert is_webhook_retryable(err) is True


def test_is_webhook_retryable_telegram_server():
    err = TelegramServerError(method=MagicMock(), message="Server error")
    assert is_webhook_retryable(err) is True


def test_is_webhook_retryable_connection_error():
    assert is_webhook_retryable(ConnectionError("reset")) is True


def test_is_webhook_retryable_postgres_connection():
    assert is_webhook_retryable(asyncpg.PostgresConnectionError("down")) is True


def test_is_webhook_retryable_bad_request():
    err = TelegramBadRequest(method=MagicMock(), message="Bad Request")
    assert is_webhook_retryable(err) is False


def test_is_webhook_retryable_unknown():
    assert is_webhook_retryable(RuntimeError("bug")) is False


def test_is_bot_to_bot_disabled_error():
    """Bots cannot receive DMs from other bots unless user allows it."""
    err = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: USER_BOT_TO_BOT_DISABLED"
    )
    assert is_bot_to_bot_disabled_error(err) is True


def test_is_bot_to_bot_disabled_error_unrelated():
    """Unrelated errors should not be classified as bot-to-bot disabled."""
    err = TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
    assert is_bot_to_bot_disabled_error(err) is False


def test_is_bot_to_bot_disabled_error_non_telegram():
    """Non-Telegram errors should not match."""
    assert is_bot_to_bot_disabled_error(RuntimeError("network")) is False


# ─── is_user_blocked_error ──────────────────────────────────────────────────


def test_is_user_blocked_error_true():
    """'bot was blocked by the user' should match TelegramForbiddenError."""
    err = TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: bot was blocked by the user"
    )
    assert is_user_blocked_error(err) is True


def test_is_user_blocked_error_case_insensitive():
    """Matching should be case-insensitive."""
    err = TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: BOT WAS BLOCKED BY THE USER"
    )
    assert is_user_blocked_error(err) is True


def test_is_user_blocked_error_wrong_type():
    """TelegramBadRequest should not match."""
    err = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: bot was blocked by the user"
    )
    assert is_user_blocked_error(err) is False


def test_is_user_blocked_error_non_telegram():
    """Non-Telegram errors should not match."""
    assert is_user_blocked_error(RuntimeError("network")) is False


# ─── is_bot_kicked_error ─────────────────────────────────────────────────────


def test_is_bot_kicked_error_from_chat():
    """'bot was kicked from the chat' should match."""
    err = TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: bot was kicked from the chat"
    )
    assert is_bot_kicked_error(err) is True


def test_is_bot_kicked_error_from_supergroup():
    """'bot was kicked from the supergroup chat' should match."""
    err = TelegramForbiddenError(
        method=MagicMock(), message="Forbidden: bot was kicked from the supergroup chat"
    )
    assert is_bot_kicked_error(err) is True


def test_is_bot_kicked_error_wrong_type():
    """TelegramBadRequest should not match."""
    err = TelegramBadRequest(
        method=MagicMock(), message="Bad Request: bot was kicked from the chat"
    )
    assert is_bot_kicked_error(err) is False


def test_is_bot_kicked_error_non_telegram():
    """Non-Telegram errors should not match."""
    assert is_bot_kicked_error(RuntimeError("network")) is False

# ─── lifecycle classification: rights vs retirement vs migration ─────────────
#
# The four exceptions the group-lifecycle sweep must tell apart. They are
# CONSTRUCTED, never mocked: the whole point is aiogram's class hierarchy, and a
# MagicMock subclass would assert whatever we told it to.

GONE = TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
NO_RIGHTS = TelegramBadRequest(
    method=MagicMock(), message="Bad Request: member list is inaccessible"
)
KICKED = TelegramForbiddenError(
    method=MagicMock(), message="Forbidden: bot was kicked from the group chat"
)
MOVED = TelegramMigrateToChat(
    method=MagicMock(),
    message="Bad Request: group chat was upgraded to a supergroup chat",
    migrate_to_chat_id=-1003721264646,
)

def test_member_list_inaccessible_is_not_group_inaccessible():
    """A rights error must never read as an inaccessible group — both directions.

    Both directions live in one test because the failure mode is a widened marker
    list: folding "member list is inaccessible" into `_INACCESSIBLE_MESSAGE_MARKERS`
    makes the first assertion pass for the wrong reason and the second one fail.
    """
    assert is_member_list_inaccessible_error(NO_RIGHTS) is True
    assert is_group_inaccessible_error(NO_RIGHTS) is False
    assert is_group_inaccessible_error(GONE) is True
    assert is_member_list_inaccessible_error(GONE) is False

def test_migrate_target_id_reads_the_upgrade_and_nothing_else():
    """Only `TelegramMigrateToChat` carries a destination id.

    The second half is the one that matters: `TelegramMigrateToChat` is not a
    `TelegramBadRequest`, so a guard written against that type would return None
    here — and the same message text on a real `TelegramBadRequest` must return
    None, because a plain upgrade error we cannot act on is not a migration.
    """
    assert migrate_target_id(MOVED) == -1003721264646
    assert is_group_inaccessible_error(MOVED) is False
    same_text = TelegramBadRequest(
        method=MagicMock(),
        message="Bad Request: group chat was upgraded to a supergroup chat",
    )
    assert migrate_target_id(same_text) is None
    assert migrate_target_id(RuntimeError("boom")) is None
