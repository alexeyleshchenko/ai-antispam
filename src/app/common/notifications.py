import contextlib
import logging
from collections.abc import Callable
from typing import cast

import logfire
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardMarkup
from tenacity import RetryError

from ..database.group_operations import cleanup_group_data
from ..database.models import GroupStatus
from .bot import bot
from .telegram_errors import (
    GROUP_ANONYMOUS_BOT_ID,
    is_bot_to_bot_disabled_error,
    is_group_inaccessible_error,
    is_message_not_found_error,
    is_user_blocked_error,
)
from .utils import format_chat_log, format_user_log

logger = logging.getLogger(__name__)


@logfire.no_auto_trace
@logfire.instrument(extract_args=True, record_return=True)
async def perform_complete_group_cleanup(
    group_id: int,
    group_title: str | None = None,
    group_username: str | None = None,
    status: GroupStatus | str = GroupStatus.LEFT,
    reason: str | None = None,
) -> bool:
    """Perform complete group cleanup: leave chat and soft-remove DB records.

    Returns success status. `status`/`reason` describe the lifecycle transition
    (deletion-policy E+C): no-rights/unpaid → PAUSED, removed/inaccessible → LEFT.
    """
    try:
        await bot.leave_chat(group_id)
    except Exception as leave_e:
        if is_group_inaccessible_error(leave_e):
            logger.info(
                f"Group {format_chat_log(group_id, group_title, group_username)} already inaccessible during leave, proceeding with DB cleanup"
            )
        else:
            logger.exception(
                f"Failed to leave group {format_chat_log(group_id, group_title, group_username)}",
            )
            return False

    try:
        await cleanup_group_data(group_id, status=status, reason=reason)
        return True
    except Exception:
        logger.exception(
            f"Failed to clean DB for group {format_chat_log(group_id, group_title, group_username)}",
        )
        return False


@logfire.no_auto_trace
@logfire.instrument(extract_args=True, record_return=True)
async def notify_admins_with_fallback_and_cleanup(
    bot,
    admin_ids: list[int],
    group_id: int,
    private_message: str | Callable[[int], str],
    group_message_template: str = "{mention}, I can't send a private message to any administrator. Please message me privately to receive important group notifications!",
    cleanup_if_group_fails: bool = True,
    parse_mode: str = "HTML",
    reply_markup: InlineKeyboardMarkup | None = None,
    assume_human_admins: bool = False,
    group_title: str | None = None,
    group_username: str | None = None,
) -> dict:
    """
    Notifies all admins in private, falls back to group if none are reachable.
    Uses the last accessible admin for the group mention.
    Cleans up group if group message fails.
    Returns a dict with results.
    """
    notified_private = []
    unreachable = []
    bots_skipped = []
    last_admin_info = None

    for admin_id in admin_ids:
        try:
            # Fast path: if admins are pre-filtered, skip expensive bot detection
            if assume_human_admins:
                # Skip bot detection entirely - trust the caller
                admin_chat = None
            else:
                # Get admin chat info with retry (expensive API call)
                async def get_chat_info(admin_id=admin_id):
                    return await bot.get_chat(admin_id)

                admin_chat = await get_chat_info()

                # Check if this is a bot account
                is_bot = False
                if getattr(admin_chat, "type", None) == "private":
                    # Primary check: API-reported bot status
                    is_bot = getattr(admin_chat, "is_bot", False)

                    # Additional check: negative IDs indicate channels/bots
                    if not is_bot and admin_id < 0:
                        is_bot = True
                        logfire.warning(
                            f"Detected channel/bot account {format_user_log(admin_id)} with negative ID"
                        )

                    # Additional check: GroupAnonymousBot (1087968824)
                    # Telegram reports is_bot=False but it can't receive bot DMs
                    if not is_bot and admin_id == GROUP_ANONYMOUS_BOT_ID:
                        is_bot = True
                        logger.info(
                            f"Skipping GroupAnonymousBot {admin_id} — Telegram forbids bot-to-bot DMs"
                        )

                if is_bot:
                    logfire.info(
                        f"Skipping bot admin {format_user_log(admin_id, getattr(admin_chat, 'first_name', None), getattr(admin_chat, 'username', None))} - cannot send messages to bots"
                    )
                    bots_skipped.append(admin_id)
                    continue

            # Resolve message (support per-admin customization)
            if callable(private_message):
                msg_text = cast("Callable[[int], str]", private_message)(admin_id)
            else:
                msg_text = private_message

            # Send message with retry
            async def send_private_message(admin_id=admin_id, msg_text=msg_text):
                return await bot.send_message(
                    admin_id,
                    msg_text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup,
                    disable_web_page_preview=True,
                )

            await send_private_message()

            notified_private.append(admin_id)
            # Use admin_chat if available, otherwise we'll handle fallback without it
            if admin_chat:
                last_admin_info = admin_chat
            logger.debug(
                f"Successfully notified admin {format_user_log(admin_id, getattr(admin_chat, 'first_name', None) if admin_chat else None, getattr(admin_chat, 'username', None) if admin_chat else None)} in private"
            )
        except Exception as e:
            # When tenacity exhausts retries, it wraps the exception in RetryError.
            # Unwrap it so existing TelegramBadRequest handlers work correctly.
            if isinstance(e, RetryError) and e.last_attempt is not None:
                e = e.last_attempt.exception()
            # Check if this is a content parsing error vs access/permission error
            if isinstance(e, TelegramBadRequest):
                # Check for specific parsing errors in the message
                error_msg = str(e)
                if (
                    "can't parse entities" in error_msg
                    or "Unsupported start tag" in error_msg
                ):
                    logger.exception(
                        f"Content parsing error when notifying admin {admin_id}: {e}. "
                        "This indicates malformed HTML in the notification message content.",
                        extra={
                            "error_message": error_msg,
                            "private_message": msg_text,
                        },
                    )
                    # Don't treat content parsing errors as "unreachable admin"
                    # These should be fixed in the message formatting, not trigger fallback
                    continue
                elif (
                    "chat not found" in error_msg.lower()
                    or is_message_not_found_error(e)
                ):
                    # User has not started a conversation with the bot — expected for
                    # admins who never messaged the bot. Log at debug, not warning.
                    logger.debug(
                        f"Cannot notify admin {format_user_log(admin_id)}: user has not started a "
                        f"conversation with the bot. Admin should send /start to enable notifications."
                    )
                    unreachable.append(admin_id)
                elif is_bot_to_bot_disabled_error(e):
                    # Recipient is a bot (e.g. GroupAnonymousBot 1087968824) that cannot
                    # receive bot-to-bot DMs. Expected and harmless — skip silently at debug.
                    logger.debug(
                        f"Cannot notify admin {format_user_log(admin_id)}: bot account cannot receive DMs "
                        f"from other bots (USER_BOT_TO_BOT_DISABLED)."
                    )
                    unreachable.append(admin_id)
                elif isinstance(e, TelegramForbiddenError):
                    if is_user_blocked_error(e):
                        # User blocked the bot — expected, non-critical. Log at debug.
                        logger.debug(
                            f"Cannot notify admin {format_user_log(admin_id)}: bot was blocked by the user. "
                            "This admin will not receive notifications until they unblock the bot."
                        )
                    else:
                        # Other TelegramForbiddenError (e.g. bot was kicked from private chat)
                        logger.debug(
                            f"Cannot notify admin {format_user_log(admin_id)}: {e}"
                        )
                    unreachable.append(admin_id)
                else:
                    # Other TelegramBadRequest errors (like invalid chat_id) should be treated as unreachable
                    logger.warning(
                        f"Telegram API error when notifying admin {format_user_log(admin_id)}: {e}",
                        exc_info=True,
                    )
                    unreachable.append(admin_id)
            else:
                logger.info(
                    f"Failed to notify admin {format_user_log(admin_id)} in private: {e}",
                    exc_info=True,
                )
                unreachable.append(admin_id)
            with contextlib.suppress(Exception):

                async def get_chat_info_fallback(admin_id=admin_id):
                    return await bot.get_chat(admin_id)

                admin_chat = await get_chat_info_fallback()
                last_admin_info = admin_chat
    result = {
        "notified_private": notified_private,
        "unreachable": unreachable,
        "bots_skipped": bots_skipped,
        "group_notified": False,
        "group_cleaned_up": False,
    }

    if notified_private:
        return result

    # No admins reachable in private, send group message
    with logfire.span("group_fallback_attempt", group_id=group_id):
        mention = None
        if last_admin_info:
            if getattr(last_admin_info, "username", None):
                # Usernames should not be escaped - they are literal text
                mention = f"@{last_admin_info.username}"
            else:
                mention = (
                    f'<a href="tg://user?id={last_admin_info.id}">админ</a>'
                    if parse_mode == "HTML"
                    else f"[админ](tg://user?id={last_admin_info.id})"
                )
            logger.info(
                f"Using mention '{mention}' for group fallback in chat {format_chat_log(group_id, group_title, group_username)}"
            )
        else:
            mention = "админ"
            logger.info(
                f"No admin info available for group fallback in chat {format_chat_log(group_id, group_title, group_username)}, using generic mention"
            )

        group_message = group_message_template.format(mention=mention)
        logger.info(
            f"Sending group fallback message to chat {format_chat_log(group_id, group_title, group_username)}: {group_message[:100]}..."
        )

        try:

            async def send_group_message():
                return await bot.send_message(
                    group_id,
                    group_message,
                    parse_mode=parse_mode,
                    disable_web_page_preview=True,
                )

            await send_group_message()
            result["group_notified"] = True
            return result
        except Exception as group_e:
            logger.info(
                f"Failed to send group fallback notification to chat {format_chat_log(group_id, group_title, group_username)}: {group_e}",
                exc_info=True,
            )
            if cleanup_if_group_fails:
                cleanup_success = await perform_complete_group_cleanup(
                    group_id,
                    group_title,
                    group_username,
                    status="left",
                    reason="notification_delivery_failure",
                )
                result["group_cleaned_up"] = cleanup_success
                if cleanup_success:
                    logger.info(
                        f"Group {format_chat_log(group_id, group_title, group_username)} cleaned up due to inability to notify admins - all notification methods failed"
                    )
            else:
                logger.info(
                    f"Cleanup disabled for group {format_chat_log(group_id, group_title, group_username)}, leaving group accessible despite notification failure"
                )

            # Log specific error if all notifications failed
            if not result["notified_private"] and not result["group_notified"]:
                logger.info(
                    f"Failed to notify admins - all notification methods failed for chat {format_chat_log(group_id, group_title, group_username)}"
                    + (", cleanup initiated" if cleanup_if_group_fails else "")
                )

            return result
