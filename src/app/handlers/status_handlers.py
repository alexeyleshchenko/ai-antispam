"""Handlers for bot status updates in chats."""

import logging
from datetime import datetime, timezone
from typing import List

import logfire
from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import or_f
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from ..common.trace_context import get_root_span

from ..common.bot import bot
from ..common.telegram_errors import (
    GROUP_ANONYMOUS_BOT_ID,
    is_bot_kicked_error,
    is_group_inaccessible_error,
    is_message_not_found_error,
    is_permission_error,
    is_user_blocked_error,
)
from ..common.notifications import notify_admins_with_fallback_and_cleanup
from ..common.utils import (
    format_chat_or_channel_display,
    get_add_to_group_url,
    retry_on_network_error,
)
from ..database import deactivate_admin, get_admin, get_group, update_group_admins
from ..database.group_operations import (
    clear_no_rights_detected_at,
    set_no_rights_detected_at,
)
from ..i18n import normalize_lang, t
from .dp import dp
from .message.channel_management import notify_channel_admins_and_leave

logger = logging.getLogger(__name__)


async def _resolve_lang(admin_ids: list[int] | None, fallback: str = "en") -> str:
    """Resolve language from first admin's language_code."""
    if not admin_ids:
        return fallback
    admin = await get_admin(admin_ids[0])
    if admin and admin.language_code:
        return normalize_lang(admin.language_code)
    return fallback


@dp.my_chat_member()
async def handle_bot_status_update(event: types.ChatMemberUpdated) -> str:
    """
    Handle updates to bot's status in chats.
    Called when bot is added to or removed from a chat.
    """
    chat_id = event.chat.id
    admin_id = event.from_user.id
    new_status = event.new_chat_member.status
    old_status = event.old_chat_member.status
    chat_title = event.chat.title or "Unnamed Group"

    # Handle updates from private chats (users blocking/unblocking the bot)
    if event.chat.type == "private":
        if new_status == "member":
            return "bot_started_private"
        elif new_status == "kicked":
            await _deactivate_admin_after_block(admin_id)
            return "bot_blocked_private"
        return "bot_status_private_other"

    try:
        if new_status == old_status:
            await _handle_permission_update(event, chat_id, admin_id, chat_title)
            return "bot_permissions_updated"

        result_tag = "bot_status_updated"

        if new_status in ["administrator", "member", "restricted"]:
            await _handle_bot_added(event, chat_id, admin_id, chat_title, new_status)
            result_tag = "bot_added_group"
        elif new_status in ["left", "kicked"]:
            await _handle_bot_removed(event, chat_id, admin_id, chat_title, new_status)
            result_tag = "bot_removed_group"

        # Если бот добавлен в канал, отправляем инструкцию с ссылкой на обсуждение (если есть)
        if event.chat.type == "channel" and new_status in [
            "administrator",
            "member",
            "restricted",
        ]:
            await notify_channel_admins_and_leave(
                event.chat, bot, adding_user=event.from_user
            )

        return result_tag

    except Exception as e:
        logger.error(
            f"Error handling bot status update in chat '{chat_title}' ({chat_id}): {e}",
            exc_info=True,
        )
        raise


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def _handle_permission_update(
    event: types.ChatMemberUpdated,
    chat_id: int,
    admin_id: int,
    chat_title: str,
) -> None:
    """Handle updates to bot's permissions."""
    if not (
        isinstance(event.old_chat_member, types.ChatMemberAdministrator)
        and isinstance(event.new_chat_member, types.ChatMemberAdministrator)
    ):
        return

    old_rights = {
        "can_delete_messages": event.old_chat_member.can_delete_messages,
        "can_restrict_members": event.old_chat_member.can_restrict_members,
    }
    new_rights = {
        "can_delete_messages": event.new_chat_member.can_delete_messages,
        "can_restrict_members": event.new_chat_member.can_restrict_members,
    }

    if old_rights != new_rights:
        has_all_rights = all(new_rights.values())

        # Если после обновления прав все еще не хватает необходимых прав
        if not has_all_rights:
            await set_no_rights_detected_at(chat_id)
            # Получаем список админов группы
            admins = await bot.get_chat_administrators(chat_id)
            admin_ids = [
                admin.user.id
                for admin in admins
                if not admin.user.is_bot and admin.user.id != GROUP_ANONYMOUS_BOT_ID
            ]
            await _notify_admins_about_rights(
                chat_id,
                chat_title,
                event.chat.username,
                admin_ids,
                assume_human_admins=True,
                is_already_admin=True,
            )
        else:
            await clear_no_rights_detected_at(chat_id)
            # Send promo message when we get all required rights
            await _send_promo_message(
                chat_id,
                chat_title,
                event.chat.username,
                [admin_id],
                admin_id,
            )
            # Send confirmation to admin
            try:
                lang = await _resolve_lang([admin_id])
                group_display = format_chat_or_channel_display(
                    chat_title, event.chat.username, t(lang, "common.group")
                )

                @retry_on_network_error
                async def send_setup_confirmation():
                    return await bot.send_message(
                        admin_id,
                        t(lang, "status.setup_done", group=group_display),
                        parse_mode="HTML",
                    )

                await send_setup_confirmation()
            except Exception as e:
                logger.warning(
                    f"Failed to send setup confirmation to admin {admin_id}: {e}",
                    exc_info=True,
                )


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def _handle_bot_added(
    event: types.ChatMemberUpdated,
    chat_id: int,
    admin_id: int,
    chat_title: str,
    new_status: str,
) -> None:
    """Handle bot being added to a group."""
    logger.info(
        f"Bot added to chat {chat_id} ('{chat_title}') with status {new_status}"
    )

    # Add only the admin who added the bot (with username if available)
    admin_username = getattr(event.from_user, "username", None)
    await update_group_admins(
        chat_id,
        [admin_id],
        [admin_username],
        group_title=chat_title if chat_title != "Unnamed Group" else None,
        group_username=getattr(event.chat, "username", None),
    )

    has_admin_rights = (
        new_status == "administrator"
        and isinstance(event.new_chat_member, types.ChatMemberAdministrator)
        and event.new_chat_member.can_delete_messages
        and event.new_chat_member.can_restrict_members
    )

    if not has_admin_rights:
        await set_no_rights_detected_at(chat_id)
        await _notify_admins_about_rights(
            chat_id,
            chat_title,
            event.chat.username,
            [admin_id],
            assume_human_admins=True,
            is_already_admin=(new_status == "administrator"),
        )
    else:
        # Only send promo message if we have admin rights
        await _send_promo_message(
            chat_id,
            chat_title,
            event.chat.username,
            [admin_id],
            admin_id,
        )
        # Send confirmation to admin
        try:
            lang = await _resolve_lang([admin_id])
            group_display = format_chat_or_channel_display(
                chat_title, event.chat.username, t(lang, "common.group")
            )

            @retry_on_network_error
            async def send_setup_confirmation():
                return await bot.send_message(
                    admin_id,
                    t(lang, "status.setup_done", group=group_display),
                    parse_mode="HTML",
                )

            await send_setup_confirmation()
        except Exception as e:
            if is_user_blocked_error(e):
                logger.debug(
                    f"Admin {admin_id} blocked the bot — skipping setup confirmation",
                )
            else:
                logger.warning(
                    f"Failed to send setup confirmation to admin {admin_id}: {e}",
                    exc_info=True,
                )


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def _handle_bot_removed(
    event: types.ChatMemberUpdated,
    chat_id: int,
    admin_id: int,
    chat_title: str,
    new_status: str,
) -> None:
    """Handle bot being removed from a group."""
    # Log who performed the removal
    removed_by = getattr(event.from_user, "first_name", "Unknown")
    removed_by_username = getattr(event.from_user, "username", None)
    removed_by_info = (
        f"{removed_by} (@{removed_by_username})" if removed_by_username else removed_by
    )

    logger.info(
        f"Bot removed from chat '{chat_title}' ({chat_id}) by {removed_by_info}"
    )

    group = await get_group(chat_id)
    if group and group.admin_ids:
        # Filter out bots from admin list - only notify human admins
        human_admin_ids = []
        human_admin_ids.extend(
            current_admin_id
            for current_admin_id in group.admin_ids
            if current_admin_id > 0
            and current_admin_id != GROUP_ANONYMOUS_BOT_ID
        )
        if human_admin_ids:
            await _notify_admins_about_removal(
                chat_id,
                chat_title,
                event.chat.username,
                human_admin_ids,
                assume_human_admins=True,
            )
        else:
            logger.warning(
                f"No human admins found for group {chat_id} to notify about bot removal"
            )


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def _notify_admins_about_rights(
    chat_id: int,
    chat_title: str,
    username: str | None,
    admin_ids: List[int],
    assume_human_admins: bool = False,
    is_already_admin: bool = False,
) -> None:
    """Notify admins about required bot permissions."""
    lang = await _resolve_lang(admin_ids)
    step_4_key = (
        "status.permission_steps_public"
        if is_already_admin
        else "status.permission_steps_private"
    )
    step_4 = t(lang, step_4_key)
    group_display = format_chat_or_channel_display(
        chat_title, username, t(lang, "common.group")
    )
    private_message = (
        t(lang, "status.hello")
        + t(lang, "status.group_label", group=group_display)
        + t(lang, "status.permission_steps")
        + step_4
        + "\n"
        + t(lang, "status.permission_steps_5")
        + t(
            lang,
            "status.permission_alternative",
            add_to_group_url=get_add_to_group_url(),
        )
    )

    await notify_admins_with_fallback_and_cleanup(
        bot,
        admin_ids,
        chat_id,
        private_message,
        group_message_template=t(lang, "common.notify_no_dm"),
        cleanup_if_group_fails=True,
        parse_mode="HTML",
        assume_human_admins=assume_human_admins,
    )


@logfire.no_auto_trace
@logfire.instrument(extract_args=True)
async def _notify_admins_about_removal(
    chat_id: int,
    chat_title: str,
    username: str | None,
    admin_ids: List[int],
    assume_human_admins: bool = False,
) -> None:
    """Notify admins when bot is removed from a group."""
    lang = await _resolve_lang(admin_ids)
    group_display = format_chat_or_channel_display(
        chat_title, username, t(lang, "common.group")
    )
    private_message = t(lang, "status.bot_removed", group=group_display)

    result = await notify_admins_with_fallback_and_cleanup(
        bot,
        admin_ids,
        chat_id,
        private_message,
        group_message_template=t(lang, "common.notify_no_dm"),
        cleanup_if_group_fails=True,
        parse_mode="HTML",
        assume_human_admins=assume_human_admins,
    )

    # If group was cleaned up, log it prominently
    if result["group_cleaned_up"]:
        logfire.warning(
            f"Group {chat_id} ('{chat_title}') was cleaned up due to inability to notify admins after bot removal"
        )


async def _send_promo_message(
    chat_id: int,
    chat_title: str,
    username: str | None,
    admin_ids: List[int],
    added_by: int,
) -> None:
    """Send promotional message to the group when bot is added."""
    try:
        lang = await _resolve_lang(admin_ids or [added_by])
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=t(lang, "status.setup_button"),
                        url=f"https://t.me/{(await bot.get_me()).username}?start=setup_{chat_id}",
                    )
                ]
            ]
        )

        promo_text = (
            t(lang, "status.group_intro")
            + t(lang, "status.spam_guide_link")
            + t(lang, "status.channel_link")
            + t(lang, "status.setup_click")
        )

        @retry_on_network_error
        async def send_promo_message():
            return await bot.send_message(
                chat_id,
                promo_text,
                reply_markup=keyboard,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        await send_promo_message()
    except Exception as e:
        logger.warning(
            f"Failed to send promo message to chat {chat_id} ('{chat_title}'): {e}"
        )


# Фильтр для сервисных сообщений о присоединении/выходе участников
member_service_message_filter = or_f(
    F.new_chat_member.as_("has_new_member"),
    F.new_chat_members.as_("has_new_members"),
    F.left_chat_member.as_("has_left_member"),
)


@dp.message(member_service_message_filter)
async def handle_member_service_message(message: types.Message) -> str:
    """
    Handle service messages about members joining or leaving the chat.
    Deletes these service messages to keep the chat clean.

    Args:
        message: The service message about member changes

    Returns:
        String indicating the result of handling
    """
    try:
        chat_id = message.chat.id
        message_id = message.message_id

        # Log the event
        if getattr(message, "new_chat_member", None) or getattr(
            message, "new_chat_members", None
        ):
            logger.info(
                f"Detected member join message in chat {chat_id} ('{message.chat.title or ''}') , message_id: {message_id}"
            )
        elif getattr(message, "left_chat_member", None):
            logger.info(
                f"Detected member leave message in chat {chat_id} ('{message.chat.title or ''}'), message_id: {message_id}"
            )

        # Delete the service message
        try:
            await bot.delete_message(chat_id, message_id)
            logger.info(
                f"Deleted service message {message_id} in chat {chat_id} ('{message.chat.title or ''}')"
            )
            return "service_message_deleted"
        except TelegramBadRequest as e:
            if is_permission_error(e):
                logger.info(
                    f"Cannot delete service message {message_id} in chat {chat_id} ('{message.chat.title or ''}'): {e}",
                    exc_info=True,
                )
                await set_no_rights_detected_at(chat_id)
                # Notify admins about missing permission - if this fails, cleanup will happen
                try:
                    admins = await bot.get_chat_administrators(chat_id)
                    admin_ids = [
                        admin.user.id
                        for admin in admins
                        if not admin.user.is_bot
                        and admin.user.id != GROUP_ANONYMOUS_BOT_ID
                    ]
                    lang = await _resolve_lang(admin_ids)
                    group_title = message.chat.title or ""
                    group_username = getattr(message.chat, "username", None)
                    group_display = format_chat_or_channel_display(
                        group_title, group_username, t(lang, "common.group")
                    )
                    private_msg = t(
                        lang,
                        "spam.no_delete_service",
                        group=group_display,
                    )
                    group_msg_tpl = t(lang, "spam.no_delete_service_group")
                    notification_result = await notify_admins_with_fallback_and_cleanup(
                        bot,
                        admin_ids,
                        chat_id,
                        private_message=private_msg,
                        group_message_template=group_msg_tpl,
                        cleanup_if_group_fails=True,
                        parse_mode="HTML",
                        assume_human_admins=True,
                    )
                    if (
                        not notification_result["notified_private"]
                        and not notification_result["group_notified"]
                    ):
                        logger.info(
                            f"Failed to notify admins about missing rights - all notification methods failed for chat {chat_id}, cleanup initiated"
                        )
                        return "service_message_no_rights_cleanup"
                    elif notification_result["group_cleaned_up"]:
                        logger.info(
                            f"Group {chat_id} cleaned up due to inability to notify admins about missing delete permissions"
                        )
                        return "service_message_no_rights_cleanup"
                    else:
                        return "service_message_no_rights"
                except Exception as notify_exc:
                    logger.info(
                        f"Failed to notify admins about missing rights: {notify_exc}"
                    )
                    return "service_message_no_rights"
            elif is_message_not_found_error(e):
                logger.debug(
                    f"Service message {message_id} already gone in chat {chat_id} ('{message.chat.title or ''}'): {e}",
                )
                return "service_message_already_deleted"
            elif is_bot_kicked_error(e):
                logger.debug(
                    f"Bot was kicked from chat {chat_id} ('{message.chat.title or ''}') — skipping delete",
                )
                return "service_message_bot_kicked"
            else:
                logger.warning(
                    f"Failed to delete service message {message_id} in chat {chat_id} ('{message.chat.title or ''}'): {e}",
                    exc_info=True,
                )
                return "service_message_delete_failed"
        except Exception as e:
            if is_bot_kicked_error(e) or is_group_inaccessible_error(e):
                logger.info(
                    f"Bot is not a member of chat {chat_id} ('{message.chat.title or ''}') — skipping delete",
                )
                return "service_message_bot_kicked"
            else:
                logger.warning(
                    f"Failed to delete service message {message_id} in chat {chat_id} ('{message.chat.title or ''}'): {e}",
                    exc_info=True,
                )
                return "service_message_delete_failed"

    except Exception as e:
        logger.error(
            f"Error handling service message in chat {chat_id} ('{message.chat.title or ''}'): {e}",
            exc_info=True,
        )
        return "service_message_error"


async def _deactivate_admin_after_block(admin_id: int) -> None:
    """Mark the administrator inactive after they block the bot."""
    try:
        if await deactivate_admin(admin_id):
            logger.info("Admin %s marked inactive after blocking the bot", admin_id)

            # Calculate total time user was with bot in days
            admin = await get_admin(admin_id)
            if admin:
                total_days = (datetime.now(timezone.utc) - admin.created_at).days

                # Set the total time on the root span for trace-level visibility
                get_root_span().set_attribute("total_user_days", total_days)
                logger.info("Set total_user_days=%d for admin %s", total_days, admin_id)
        else:
            logger.info("Admin %s was already inactive when blocking the bot", admin_id)
    except Exception as exc:
        logger.warning(
            "Failed to deactivate admin %s after blocking the bot: %s",
            admin_id,
            exc,
            exc_info=True,
        )
