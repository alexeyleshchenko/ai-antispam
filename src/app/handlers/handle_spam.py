"""Spam message handling: notifications, deletion, banning."""

import asyncio
import html
import logging
from collections.abc import Awaitable, Callable, Sequence
from functools import wraps

from aiogram import types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from ..common.bot import bot
from ..common.mcp_client import McpHttpError, get_mcp_client
from ..common.notifications import notify_admins_with_fallback_and_cleanup
from ..common.telegram_errors import (
    is_group_inaccessible_error,
    is_message_not_found_error,
    is_permission_error,
)
from ..common.utils import (
    determine_effective_user_id,
    format_chat_log,
    format_chat_or_channel_display,
    format_user_log,
    get_project_channel_url,
    get_setup_guide_url,
    get_spam_guide_url,
    spam_notify_spammers_via_mcp_enabled,
    user_display_label,
)
from ..database import get_admin, get_admins_map
from ..database.group_operations import (
    remove_member_from_group,
    set_no_rights_detected_at,
)
from ..database.models import Administrator
from ..database.spam_examples import insert_pending_spam_example
from ..i18n import normalize_lang, resolve_lang, t
from ..spam.account_signals import build_account_signals_body
from ..types import MessageContextResult, MessageNotificationContext

logger = logging.getLogger(__name__)


def telegram_action(
    action_name: str,
    *,
    extra_checks: Sequence[Callable[[Exception], bool]] = (),
    on_permission_error: Callable[[Exception], Awaitable[None]] | None = None,
) -> Callable:
    """Decorator: standardizes exception handling for Telegram moderator API calls.

    Catches:
    - (TelegramBadRequest, TelegramForbiddenError):
        * group_inaccessible → no-op (group is gone)
        * extra_checks match → no-op (e.g. message already deleted)
        * on_permission_error → call async callback (for UI feedback)
        * else → log warning
    - Exception → log warning (NEVER crashes)

    Note: retry is NOT here — handled by session middleware (bot.py:setup_session_retry).
    """

    def decorator(
        fn: Callable[..., Awaitable[object]],
    ) -> Callable[..., Awaitable[None]]:
        @wraps(fn)
        async def wrapper(*args: object, **kwargs: object) -> None:
            try:
                await fn(*args, **kwargs)
                logger.info(
                    "%s succeeded for %s",
                    action_name,
                    _caller_context(args),
                )
            except (TelegramBadRequest, TelegramForbiddenError) as e:
                if is_group_inaccessible_error(e):
                    logger.info(
                        "%s skipped (group inaccessible): %s",
                        action_name,
                        e,
                    )
                    return
                for check in extra_checks:
                    if check(e):
                        logger.info(
                            "%s already resolved: %s",
                            action_name,
                            e,
                        )
                        return
                if is_permission_error(e):
                    if on_permission_error is not None:
                        await on_permission_error(e)
                    else:
                        logger.warning(
                            "%s failed (permission error): %s",
                            action_name,
                            e,
                            exc_info=True,
                        )
                    return
                logger.warning(
                    "%s failed (Telegram error): %s",
                    action_name,
                    e,
                    exc_info=True,
                )
            except Exception as e:
                logger.warning(
                    "%s failed (unexpected): %s",
                    action_name,
                    e,
                    exc_info=True,
                )

        return wrapper

    return decorator


def _caller_context(args: tuple[object, ...]) -> str:
    """Extract minimal context from decorator args for success logging."""
    if not args:
        return ""
    first = args[0]
    # message object
    if hasattr(first, "chat") and hasattr(first, "message_id"):
        chat_id = getattr(first.chat, "id", "?")
        msg_id = first.message_id  # type: ignore[attr-defined]
        return f"chat={chat_id} msg={msg_id}"
    # raw chat_id + user_id
    if isinstance(first, int) and len(args) >= 2 and isinstance(args[1], int):
        return f"chat={first} user={args[1]}"
    return str(first)[:80]


async def _get_notification_lang(
    admin_ids: list[int],
    fallback_user: types.User | None = None,
) -> str:
    """Resolve language for spam notifications from admin preferences or user fallback."""
    if not admin_ids:
        return "en"
    admin = await get_admin(admin_ids[0])
    if admin and admin.language_code:
        return normalize_lang(admin.language_code)
    return resolve_lang(fallback_user, None) if fallback_user else "en"


async def handle_spam(
    message: types.Message,
    admin_ids: list[int],
    reason: str | None = None,
    message_context_result: MessageContextResult | None = None,
    skip_auto_delete: bool = False,
    is_low_confidence_not_spam: bool = False,
    confidence: int | None = None,
) -> str:
    """Handle detected spam: notify admins, optionally delete and ban."""
    if not message.from_user:
        logger.warning("Message without user info, skipping spam handling")
        return "spam_no_user_info"

    all_admins_delete = await check_admin_delete_preferences(admin_ids)
    effective_all_admins_delete = all_admins_delete and not skip_auto_delete

    notification_sent = await notify_admins(
        message,
        effective_all_admins_delete,
        admin_ids,
        reason,
        message_context_result,
        is_low_confidence_not_spam=is_low_confidence_not_spam,
        confidence=confidence,
    )

    if (
        message_context_result
        and message_context_result.linked_channel_found
        and not is_low_confidence_not_spam
    ):
        await notify_spam_contacts_via_mcp(message, reason, message_context_result)

    if effective_all_admins_delete:
        effective_user_id = determine_effective_user_id(message)
        if effective_user_id is None:
            logger.warning("Message without effective user info, skipping ban")
            return "spam_no_user_info"
        results = await asyncio.gather(
            handle_spam_message_deletion(message, admin_ids),
            ban_user_for_spam(
                message.chat.id,
                effective_user_id,
                admin_ids,
                message.chat.title,
                group_username=getattr(message.chat, "username", None),
            ),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.warning("auto-delete+ban partial failure: %s", r)
        return "spam_auto_deleted"

    return "spam_admins_notified" if notification_sent else "spam_notification_failed"


async def check_admin_delete_preferences(admin_ids: list[int]) -> bool:
    """Return True if all admins have auto-delete enabled (delete or delete_silent)."""
    if not admin_ids:
        logger.warning("check_admin_delete_preferences: no admin_ids provided")
        return False

    admins_map = await get_admins_map(admin_ids)
    for admin_id in admin_ids:
        admin_user = admins_map.get(admin_id)
        if not admin_user:
            logger.warning(
                "check_admin_delete_preferences: admin id=%s not found in DB "
                "(no username/name available)",
                admin_id,
            )
            return False
        if not admin_user.auto_deletes_spam:
            logger.info(
                "check_admin_delete_preferences: admin %s opted out of auto-delete (mode=%s)",
                user_display_label(
                    username=admin_user.username,
                    user_id=admin_id,
                ),
                admin_user.moderation_mode.value,
            )
            return False
    return True


def filter_admins_for_auto_delete_notification(
    admin_ids: list[int],
    admins_map: dict[int, Administrator],
) -> list[int]:
    """Drop delete_silent admins from auto-delete confirmation DMs."""
    return [
        aid
        for aid in admin_ids
        if not (admins_map.get(aid) and admins_map[aid].skips_auto_delete_notification)
    ]


def create_admin_notification_keyboard(
    message: types.Message,
    all_admins_delete: bool,
    pending_id: int | None = None,
    *,
    lang: str = "en",
) -> InlineKeyboardMarkup:
    """Build keyboard with Delete/Not spam buttons for admin notification."""
    effective_user_id = determine_effective_user_id(message)
    if effective_user_id is None or pending_id is None:
        return InlineKeyboardMarkup(inline_keyboard=[[]])

    if not all_admins_delete:
        row = [
            InlineKeyboardButton(
                text=t(lang, "spam.delete_button"),
                callback_data=f"delete_spam_message:{effective_user_id}:{message.chat.id}:{message.message_id}",
                style="danger",
            ),
            InlineKeyboardButton(
                text=t(lang, "spam.not_spam_button"),
                callback_data=f"mark_as_not_spam:{pending_id}",
                style="success",
            ),
        ]
    else:
        row = [
            InlineKeyboardButton(
                text=t(lang, "spam.not_spam_confirm"),
                callback_data=f"mark_as_not_spam:{pending_id}",
                style="success",
            ),
        ]
    return InlineKeyboardMarkup(inline_keyboard=[row])


def format_missing_permission_message(
    chat_title: str,
    permission_name: str,
    chat_username: str | None = None,
    *,
    lang: str = "en",
) -> str:
    """Format message about missing bot permissions for admins."""
    perm_key = (
        "spam.permission_delete_action"
        if ("delete" in permission_name.lower() or "удал" in permission_name.lower())
        else "spam.permission_ban_action"
    )
    action_description = t(lang, perm_key)

    group_display = format_chat_or_channel_display(
        chat_title, chat_username, t(lang, "common.group")
    )
    return t(
        lang,
        "spam.no_permission",
        action=action_description,
        permission=permission_name,
        group=group_display,
    ) + t(lang, "spam.setup_guide_link", url=get_setup_guide_url())


async def handle_permission_error(
    error: Exception,
    chat_id: int,
    admin_ids: list[int] | None,
    group_title: str | None,
    permission_name: str,
    action_description: str,
    group_username: str | None = None,
    *,
    lang: str = "en",
) -> bool:
    """Detect permission error, notify admins, return True if it was a permission error."""
    if not is_permission_error(error):
        return False

    logger.warning(
        f"Cannot {action_description} in chat {format_chat_log(chat_id, group_title, group_username)}: {error}",
        exc_info=error,
    )
    await set_no_rights_detected_at(chat_id)
    if admin_ids:
        try:
            display_title = group_title or str(chat_id)
            group_msg_tpl = t(
                lang,
                "spam.group_no_permission",
                mention="{mention}",
                permission=permission_name,
            )
            await notify_admins_with_fallback_and_cleanup(
                bot,
                admin_ids,
                chat_id,
                private_message=format_missing_permission_message(
                    display_title, permission_name, group_username, lang=lang
                ),
                group_message_template=group_msg_tpl
                + t(lang, "spam.setup_guide_link", url=get_setup_guide_url()),
                cleanup_if_group_fails=True,
                parse_mode="HTML",
            )
        except Exception as notify_exc:  # noqa: BLE001
            logger.warning(
                f"Failed to notify admins about missing rights for {action_description}: {notify_exc}"
            )
    return True


def format_admin_notification_message(
    context: MessageNotificationContext,
    all_admins_delete: bool,
    reason: str | None = None,
    *,
    lang: str = "en",
    is_low_confidence_not_spam: bool = False,
    confidence: int | None = None,
    include_mode_tip: bool = True,
) -> str:
    """Format spam notification text for admins."""
    if context.effective_user_id is None:
        return t(lang, "spam.error_no_user")

    group_display = format_chat_or_channel_display(
        context.chat_title, context.chat_username, t(lang, "common.group")
    )
    violator_display = format_chat_or_channel_display(
        context.violator_name, context.violator_username, t(lang, "common.user")
    )

    reason_text = (
        t(lang, "spam.reason_label", reason=html.escape(reason or "", quote=True))
        if reason
        else ""
    )

    if all_admins_delete:
        # Informational: spam was auto-deleted, no action needed
        title = t(lang, "spam.deleted_title")
        hint = ""
    elif is_low_confidence_not_spam:
        # Action required: low-confidence not-spam needs review
        title = t(lang, "spam.review_low_confidence_title")
        hint = (
            t(lang, "spam.review_low_confidence_hint", confidence=confidence or 0)
            if confidence is not None
            else ""
        )
    else:
        # Action required: spam needs Delete / Not spam confirmation
        title = t(lang, "spam.needs_confirmation_title")
        hint = ""

    admin_msg = (
        title
        + t(lang, "spam.group_label", display=group_display)
        + t(lang, "spam.violator_label", display=violator_display)
        + t(lang, "spam.content_label", content=context.content_text)
        + f"{reason_text}{context.forward_source}\n"
    )
    if hint:
        admin_msg += hint + "\n\n"

    if all_admins_delete:
        key = (
            "spam.destroyed_channel"
            if context.is_channel_sender
            else "spam.destroyed_user"
        )
        admin_msg += t(lang, key)
    else:
        link = context.message_link or get_project_channel_url()
        admin_msg += t(lang, "spam.message_link", link=link)
        if include_mode_tip:
            admin_msg += t(lang, "spam.mode_tip")

    admin_msg += (
        "\n\n" + f'<a href="{get_spam_guide_url()}">' + t(lang, "spam.spam_guide_link")
    )

    return admin_msg


async def notify_admins(
    message: types.Message,
    all_admins_delete: bool,
    admin_ids: list[int],
    reason: str | None = None,
    message_context_result: MessageContextResult | None = None,
    is_low_confidence_not_spam: bool = False,
    confidence: int | None = None,
) -> bool:
    """Notify admins about spam. Returns True if at least one notification succeeded."""
    if not message.from_user:
        return False

    lang = await _get_notification_lang(admin_ids, message.from_user)

    context = MessageNotificationContext.from_message(message)

    recipient_ids = admin_ids
    if all_admins_delete:
        admins_map = await get_admins_map(admin_ids)
        recipient_ids = filter_admins_for_auto_delete_notification(
            admin_ids, admins_map
        )
        if not recipient_ids:
            return False

    # When confirmation needed, hide mode_tip for admins already in delete mode
    if all_admins_delete:
        private_message = format_admin_notification_message(
            context,
            all_admins_delete,
            reason,
            lang=lang,
            is_low_confidence_not_spam=is_low_confidence_not_spam,
            confidence=confidence,
        )
    else:
        admins_map = await get_admins_map(admin_ids)

        def message_for_admin(admin_id: int) -> str:
            admin = admins_map.get(admin_id)
            include_mode_tip = not (admin and admin.auto_deletes_spam)
            return format_admin_notification_message(
                context,
                all_admins_delete,
                reason,
                lang=lang,
                is_low_confidence_not_spam=is_low_confidence_not_spam,
                confidence=confidence,
                include_mode_tip=include_mode_tip,
            )

        private_message = message_for_admin

    pending_id = None
    if context.effective_user_id is not None and recipient_ids:
        text = "[MEDIA_MESSAGE]"
        name = None
        bio = None
        linked_channel_fragment = None
        stories_context = None
        reply_context = None
        account_signals_context = None
        if message_context_result:
            ctx = message_context_result.context
            text = message_context_result.message_text or "[MEDIA_MESSAGE]"
            name = ctx.name if ctx else None
            bio = ctx.bio if ctx else None
            if ctx:
                if ctx.linked_channel:
                    frag = ctx.linked_channel.get_fragment()
                    if frag:
                        linked_channel_fragment = frag
                if ctx.stories:
                    stories_context = ctx.stories.get_fragment("[EMPTY]")
                reply_context = ctx.reply
                account_signals_context = build_account_signals_body(ctx)
        pending_id = await insert_pending_spam_example(
            message.chat.id,
            message.message_id,
            context.effective_user_id,
            text=text,
            name=name,
            bio=bio,
            linked_channel_fragment=linked_channel_fragment,
            stories_context=stories_context,
            reply_context=reply_context,
            account_signals_context=account_signals_context,
        )

    keyboard = create_admin_notification_keyboard(
        message, all_admins_delete, pending_id, lang=lang
    )
    result = await notify_admins_with_fallback_and_cleanup(
        bot,
        recipient_ids,
        message.chat.id,
        private_message,
        group_message_template=t(lang, "spam.group_message_template"),
        cleanup_if_group_fails=True,
        parse_mode="HTML",
        reply_markup=keyboard,
    )
    return bool(result["notified_private"]) or bool(result["group_notified"])


async def handle_spam_message_deletion(
    message: types.Message, admin_ids: list[int]
) -> None:
    """Delete the spam message; notify admins on permission failure."""
    if not message.from_user:
        return

    async def _notify_permission_error(_e: Exception) -> None:
        lang = await _get_notification_lang(admin_ids)
        perm_name = t(lang, "spam.permission_delete")
        await handle_permission_error(
            _e,
            message.chat.id,
            admin_ids,
            message.chat.title,
            perm_name,
            "delete spam message",
            getattr(message.chat, "username", None),
            lang=lang,
        )

    @telegram_action(
        "delete spam message",
        extra_checks=(is_message_not_found_error,),
        on_permission_error=_notify_permission_error,
    )
    async def _delete() -> None:
        await bot.delete_message(message.chat.id, message.message_id)

    await _delete()


async def ban_user_for_spam(
    chat_id: int,
    user_id: int,
    admin_ids: list[int] | None = None,
    group_title: str | None = None,
    group_username: str | None = None,
) -> None:
    """Ban user/channel in chat and remove from approved_members."""

    async def _notify_ban_permission_error(_e: Exception) -> None:
        lang = await _get_notification_lang(admin_ids or [])
        perm_name = t(lang, "spam.permission_ban")
        await handle_permission_error(
            _e,
            chat_id,
            admin_ids,
            group_title,
            perm_name,
            "ban user",
            group_username,
            lang=lang,
        )

    @telegram_action(
        "ban user",
        on_permission_error=_notify_ban_permission_error,
    )
    async def _ban() -> None:
        if user_id < 0:
            await bot.ban_chat_sender_chat(chat_id, sender_chat_id=user_id)
        else:
            await bot.ban_chat_member(chat_id, user_id)

    await _ban()

    try:
        await remove_member_from_group(user_id, chat_id)
    except Exception as e:
        logger.warning(
            f"Failed to remove user {format_user_log(user_id)} from approved_members: {e}",
            exc_info=True,
        )


def build_spam_block_notification_message(
    context: MessageNotificationContext,
    reason: str | None = None,
    *,
    lang: str = "en",
) -> str:
    """Build notification message for MCP spam notifications to spammers/channel admins."""
    from ..common.utils import load_config

    config = load_config()
    project_website = config["system"]["project_website"]
    project_channel = get_project_channel_url()

    entity_display = format_chat_or_channel_display(
        context.entity_name, context.entity_username, t(lang, "common.channel")
    )
    entity_type_key = (
        "spam.entity_channel"
        if context.entity_type == "канале"
        else "spam.entity_group"
    )
    entity_type = t(lang, entity_type_key)
    notification_msg = t(
        lang,
        "spam.blocked_comment",
        entity_type=entity_type,
        entity_display=entity_display,
        content=context.content_text,
    )

    if reason:
        notification_msg += t(
            lang,
            "spam.block_reason",
            reason=html.escape(reason, quote=True),
        )

    notification_msg += t(
        lang,
        "spam.site_channel",
        website=project_website,
        channel=project_channel,
    )

    return notification_msg


async def send_mcp_message_to_user(
    *,
    user_id: int,
    username: str | None,
    message: str,
    message_type: str,
) -> bool:
    client = get_mcp_client()
    chat_identifier = f"@{username}" if username else str(user_id)
    log_extra = {
        "user_id": user_id,
        "username": username,
        "chat_identifier": chat_identifier,
        "message_type": message_type,
    }
    try:
        await client.call_tool(
            "send_message",
            arguments={
                "chat_id": chat_identifier,
                "message": message,
                "parse_mode": "html",
            },
        )
        logger.info("Sent MCP spam notification", extra=log_extra)
        return True
    except McpHttpError as e:
        logger.warning(
            "Failed to send MCP message",
            extra={**log_extra, "error": str(e)},
            exc_info=True,
        )
        return False
    except Exception as e:
        logger.exception(
            "Unexpected error sending MCP message",
            extra={**log_extra, "error": str(e)},
        )
        return False


async def notify_spam_contacts_via_mcp(
    message: types.Message,
    reason: str | None = None,
    message_context_result: MessageContextResult | None = None,
) -> None:
    """Send MCP promotional notifications to spammers and spamming channel admins."""
    if not spam_notify_spammers_via_mcp_enabled():
        logger.debug(
            "Skipping MTProto spammer notifications because feature flag is off"
        )
        return

    channel_users = (
        message_context_result.channel_users if message_context_result else None
    )
    context = MessageNotificationContext.from_message(message)
    notification_msg = build_spam_block_notification_message(context, reason)

    # Check if this is a channel sender
    is_channel_sender = (
        message.sender_chat is not None and message.sender_chat.id != message.chat.id
    )

    if is_channel_sender and channel_users:
        # For channel senders, send to channel admins (filter out bots)
        admin_count = 0
        for user in channel_users:
            if user.get("bot", True):
                continue  # Skip bots

            user_id = user.get("id")
            username = user.get("username")

            if user_id:
                success = await send_mcp_message_to_user(
                    user_id=user_id,
                    username=username,
                    message=notification_msg,
                    message_type="spammer_channel_admin_notification",
                )
                if success:
                    admin_count += 1

        logger.info(
            f"Sent spam notifications to {admin_count} channel admins",
            extra={
                "channel_id": message.sender_chat.id if message.sender_chat else None,
                "total_admins": len(
                    [u for u in channel_users if not u.get("bot", True)]
                ),
                "successful_notifications": admin_count,
            },
        )

    elif not is_channel_sender and message.from_user:
        # For human senders only - never use message.from_user for channel posts
        # (Telegram uses Channel Bot 136817688 as from_user for channel messages)
        user_id = message.from_user.id
        username = getattr(message.from_user, "username", None)
        success = await send_mcp_message_to_user(
            user_id=user_id,
            username=username,
            message=notification_msg,
            message_type="spammer_user_notification",
        )

        if not success:
            return
