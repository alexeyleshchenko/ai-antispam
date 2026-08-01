import asyncio
import contextlib
import logging

from aiogram import F, types
from aiogram.exceptions import TelegramBadRequest
from ..common.telegram_errors import (
    is_message_not_found_error,
    is_message_not_modified_error,
    is_permission_error,
)
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ..common.bot import bot
from ..common.utils import get_add_to_group_url, format_chat_log, retry_on_network_error
from ..database import get_admin, get_group, update_admin_language
from ..common.utils import load_config
from ..database.group_operations import add_member, set_moderation_events
from ..database.spam_examples import (
    confirm_pending_example_as_not_spam,
    confirm_pending_example_as_spam,
)
from ..i18n import HELP_PAGE_CALLBACK_KEYS, resolve_lang, t
from .dp import dp
from .handle_spam import ban_user_for_spam

logger = logging.getLogger(__name__)


async def _answer_safe(
    callback: CallbackQuery, text: str | None = None, *, show_alert: bool = False
) -> None:
    """Answer a callback, swallowing errors (already answered / query too old).

    The Delete-spam handler acks the callback immediately (before any network
    work) so the Telegram client stops retrying the press — a slow answer causes
    duplicate updates with fresh update_ids (see #33). Any later outcome popup is
    then a no-op, so all answers go through this safe wrapper.
    """
    with contextlib.suppress(Exception):
        if text is None:
            await callback.answer()
        else:
            await callback.answer(text, show_alert=show_alert)


@dp.callback_query(F.data.startswith("lang_set:"))
async def handle_lang_set_callback(callback: CallbackQuery) -> str:
    """Handle language selection. Callback data: lang_set:ru or lang_set:en."""
    if not callback.data or not callback.message or not callback.from_user:
        return "callback_invalid_data"
    parts = callback.data.split(":")
    if len(parts) != 2 or parts[1] not in ("ru", "en"):
        return "callback_invalid_lang"
    lang = parts[1]
    admin_id = callback.from_user.id
    await update_admin_language(admin_id, lang)
    confirm_text = (
        t(lang, "lang.changed_ru") if lang == "ru" else t(lang, "lang.changed_en")
    )
    await callback.answer(confirm_text, show_alert=False)
    if callback.message and isinstance(callback.message, types.Message):
        await callback.message.edit_text(confirm_text, parse_mode="HTML")
    return "callback_lang_set"


@dp.callback_query(F.data.in_(HELP_PAGE_CALLBACK_KEYS))
async def handle_help_pages(callback: CallbackQuery) -> str:
    """Show a help subsection; callback_data is the same string as the t() key."""
    if not callback.message or not isinstance(callback.message, types.Message):
        await callback.answer(t("en", "callback.message_inaccessible"), show_alert=True)
        return "callback_message_inaccessible"

    admin_id = callback.from_user.id if callback.from_user else 0
    admin = await get_admin(admin_id)
    lang = resolve_lang(callback.from_user, admin)

    callback_data = callback.data or ""
    if callback_data == "help.getting_started":
        text = t(lang, callback_data, add_to_group_url=get_add_to_group_url())
    else:
        text = t(lang, callback_data)
    if text == callback_data:
        text = t(lang, "help.default_page")

    back_button = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "help.back_button"),
                    callback_data="help_back",
                )
            ]
        ]
    )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=back_button,
        disable_web_page_preview=True,
    )
    await callback.answer()

    return f"{callback_data}_shown"


@dp.callback_query(F.data == "help_back")
async def handle_help_back(callback: CallbackQuery) -> str:
    """Return to the main help menu."""
    if not callback.message or not isinstance(callback.message, types.Message):
        await callback.answer(t("en", "callback.message_inaccessible"), show_alert=True)
        return "callback_message_inaccessible"

    admin_id = callback.from_user.id if callback.from_user else 0
    admin = await get_admin(admin_id)
    lang = resolve_lang(callback.from_user, admin)

    text = t(lang, "help.main")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.getting_started"),
                    callback_data="help.getting_started",
                ),
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.training"),
                    callback_data="help.training",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.moderation"),
                    callback_data="help.moderation",
                ),
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.commands"),
                    callback_data="help.commands",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.payment"),
                    callback_data="help.payment",
                ),
                InlineKeyboardButton(
                    text=t(lang, "help.buttons.support"),
                    callback_data="help.support",
                ),
            ],
        ]
    )

    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard,
        disable_web_page_preview=True,
    )
    await callback.answer()
    return "help_back_shown"


@dp.callback_query(F.data.startswith("mark_as_not_spam:"))
async def handle_spam_ignore_callback(callback: CallbackQuery) -> str:
    """
    Обработчик колбэка для подтверждения сообщения как не спам.
    Callback data: mark_as_not_spam:{pending_id}
    """
    try:
        admin_id = callback.from_user.id

        if not callback.data or not callback.message:
            return "callback_invalid_data"

        admin = await get_admin(admin_id)
        lang = resolve_lang(callback.from_user, admin)
        with contextlib.suppress(Exception):
            await callback.answer(
                f"✅ {t(lang, 'callback.safe_added')}", show_alert=False
            )
        parts = callback.data.split(":")
        if len(parts) < 2:
            return "callback_invalid_data_format"

        try:
            pending_id = int(parts[1])
        except ValueError:
            return "callback_invalid_data_format"

        row = await confirm_pending_example_as_not_spam(pending_id, admin_id)

        message = callback.message
        if not isinstance(message, types.Message):
            return "callback_invalid_message_type"

        # Preserve HTML formatting (message.text is plain text; entities hold formatting)
        message_text = (
            getattr(message, "html_text", None) or message.text or message.caption or ""
        )
        updated_message_text = (
            f"{message_text}\n\n✅ <b>{t(lang, 'callback.safe_added')}</b>"
        )

        if row and "chat_id" in row:
            group_id = row["chat_id"]
            effective_user_id = row["effective_user_id"]

            async with asyncio.TaskGroup() as tg:
                if effective_user_id < 0:
                    tg.create_task(
                        bot.unban_chat_sender_chat(
                            group_id, sender_chat_id=effective_user_id
                        )
                    )
                else:
                    tg.create_task(
                        bot.unban_chat_member(
                            group_id, effective_user_id, only_if_banned=True
                        )
                    )
                tg.create_task(add_member(group_id, effective_user_id))
                probation_min = int(
                    load_config().get("spam", {}).get("probation_min_events", 3)
                )
                tg.create_task(
                    set_moderation_events(group_id, effective_user_id, probation_min)
                )
                tg.create_task(
                    bot.edit_message_text(
                        chat_id=callback.message.chat.id,
                        message_id=callback.message.message_id,
                        text=updated_message_text,
                        parse_mode="HTML",
                        reply_markup=None,
                    )
                )
        elif row and row.get("already_confirmed"):
            logger.debug(
                "mark_as_not_spam: pending %s already handled (duplicate tap), skipping",
                pending_id,
            )
            with contextlib.suppress(Exception):
                await bot.edit_message_text(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    text=updated_message_text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
        else:
            logger.warning(
                "mark_as_not_spam: pending record %s not found, skipping unban/add",
                pending_id,
            )
            with contextlib.suppress(Exception):
                await bot.edit_message_text(
                    chat_id=callback.message.chat.id,
                    message_id=callback.message.message_id,
                    text=updated_message_text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
        return "callback_marked_as_not_spam"

    except Exception as e:
        logger.error(f"Error in spam ignore callback: {e}", exc_info=True)
        with contextlib.suppress(Exception):
            admin = (
                await get_admin(callback.from_user.id) if callback.from_user else None
            )
            lang = resolve_lang(callback.from_user, admin)
            await callback.answer(t(lang, "callback.error_generic"), show_alert=True)
        return "callback_error_marking_not_spam"


@dp.callback_query(F.data.startswith("delete_spam_message:"))
async def handle_spam_confirm_callback(callback: CallbackQuery) -> str:
    """
    Handle "Delete" button in notify mode: delete original message, ban spammer,
    remove notification keyboard. Marks pending spam example as confirmed.
    """
    if not callback.data or not callback.from_user:
        return "callback_invalid_data"

    admin = await get_admin(callback.from_user.id)
    lang = resolve_lang(callback.from_user, admin)

    try:
        _, effective_user_id_str, chat_id_str, message_id_str = callback.data.split(":")
        effective_user_id = int(effective_user_id_str)
        chat_id = int(chat_id_str)
        message_id = int(message_id_str)

        if not callback.message:
            logger.warning("No notification message in callback")
            await callback.answer(t(lang, "callback.invalid_callback"), show_alert=True)
            return "callback_invalid_message"

        # Ack immediately, before any network work, so the Telegram client stops
        # retrying the press (duplicate updates — see #33). Outcome feedback is
        # conveyed via the notification-message edit below.
        await _answer_safe(callback)

        delete_succeeded = False
        try:

            @retry_on_network_error
            async def delete_original_message():
                return await bot.delete_message(chat_id, message_id)

            await delete_original_message()
            delete_succeeded = True
        except TelegramBadRequest as e:
            if is_message_not_found_error(e):
                logger.info(
                    f"Message to delete not found (likely already deleted): {format_chat_log(chat_id)}:{message_id}"
                )
                delete_succeeded = True
            elif is_permission_error(e):
                logger.info(
                    f"Cannot delete message (no permission or too old): {format_chat_log(chat_id)}:{message_id}: {e}"
                )
                await _answer_safe(
                    callback, t(lang, "callback.delete_no_permission"), show_alert=True
                )
            else:
                logger.warning(
                    f"Failed to delete original spam message: {e}", exc_info=True
                )
                await _answer_safe(
                    callback, t(lang, "callback.delete_failed"), show_alert=True
                )
                return "callback_error_deleting_original"
        except Exception as e:
            logger.warning(
                f"Failed to delete original spam message: {e}", exc_info=True
            )
            await _answer_safe(
                callback, t(lang, "callback.delete_failed"), show_alert=True
            )
            return "callback_error_deleting_original"

        if delete_succeeded:
            await _answer_safe(callback, f"✅ {t(lang, 'callback.spam_deleted')}")

        await confirm_pending_example_as_spam(
            chat_id, message_id, callback.from_user.id
        )

        group = await get_group(chat_id)
        admin_ids = group.admin_ids if group else None
        await ban_user_for_spam(chat_id, effective_user_id, admin_ids, group_title=None)

        # Remove keyboard and append confirmation line (Callback Button UX Pattern)
        msg = callback.message
        if isinstance(msg, types.Message):
            # Preserve HTML formatting (message.text is plain text; entities hold formatting)
            existing_text = (
                getattr(msg, "html_text", None) or msg.text or msg.caption or ""
            )
            if delete_succeeded:
                updated_text = (
                    f"{existing_text}\n\n✅ <b>{t(lang, 'callback.spam_deleted')}</b>"
                )
            else:
                updated_text = (
                    f"{existing_text}\n\n⚠️ <b>{t(lang, 'callback.delete_no_permission')}</b>"
                )
            try:
                await bot.edit_message_text(
                    chat_id=msg.chat.id,
                    message_id=msg.message_id,
                    text=updated_text,
                    parse_mode="HTML",
                    reply_markup=None,
                )
            except TelegramBadRequest as e:
                if not is_message_not_found_error(e) and not is_message_not_modified_error(e):
                    logger.warning(
                        "Failed to edit notification message %s:%s (admin=%s): %s",
                        msg.chat.id,
                        msg.message_id,
                        callback.from_user.id if callback.from_user else None,
                        e,
                    )

        return "callback_spam_message_deleted"

    except Exception as e:
        logger.error(f"Error in spam confirm callback: {e}", exc_info=True)
        with contextlib.suppress(Exception):
            await callback.answer(t(lang, "callback.error_generic"), show_alert=True)
        return "callback_error_deleting_spam"
