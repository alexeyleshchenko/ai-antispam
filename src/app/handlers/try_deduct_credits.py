"""
Модуль для управления кредитами и деактивацией групп.

Содержит функции для:
- Списания кредитов с администраторов групп
- Деактивации модерации при недостатке кредитов
- Уведомления администраторов о деактивации
- Поиска администраторов с минимальным количеством кредитов
"""

import asyncio
import logging
from collections.abc import Sequence

import logfire
from aiogram.types import ChatMember, ChatMemberAdministrator, ChatMemberOwner

from ..common.bot import bot
from ..common.notifications import notify_admins_with_fallback_and_cleanup
from ..common.utils import (
    format_chat_log,
    format_chat_or_channel_display,
    get_add_to_group_url,
)
from ..database import deduct_credits_from_admins, get_admin, set_group_moderation
from ..i18n import normalize_lang, t

logger = logging.getLogger(__name__)


async def try_deduct_credits(chat_id: int, amount: int, reason: str) -> bool:
    """
    Попытка списать звезды у админов. При неудаче отключает модерацию.

    Args:
        chat_id: ID чата
        amount: Количество списываемых звезд
        reason: Причина списания

    Returns:
        bool: True если списание успешно, False иначе
    """
    if amount == 0:
        return True

    admin_id = await deduct_credits_from_admins(chat_id, amount)

    if not admin_id:
        title = username = None
        try:
            chat = await bot.get_chat(chat_id)
            title = getattr(chat, "title", None)
            username = getattr(chat, "username", None)
        except Exception:  # noqa: BLE001
            title = username = None
        logger.warning(
            f"No paying admins in chat {format_chat_log(chat_id, title, username)} for {reason}"
        )
        await handle_deactivation(chat_id)
        return False

    logger.debug(f"Deducted {amount}★ from {admin_id} in {format_chat_log(chat_id)}")
    return True


@logfire.instrument(extract_args=True, record_return=True)
async def handle_deactivation(chat_id: int) -> None:
    """
    Обрабатывает деактивацию группы.

    Args:
        chat_id: ID чата
    """
    # Fetch chat metadata first (best-effort): the deactivation itself must
    # still happen even if the fetch fails, but when we CAN get the info we
    # pass it into set_group_moderation so a fresh row is never created bare
    # (no-metadata gap). COALESCE keeps existing values when the fetch failed.
    title = username = None
    try:
        chat = await bot.get_chat(chat_id)
        title = getattr(chat, "title", None)
        username = getattr(chat, "username", None)
    except Exception:  # noqa: BLE001
        logger.warning(
            f"Failed to get chat info for {format_chat_log(chat_id)} during deactivation"
        )

    await set_group_moderation(chat_id, False, title, username)

    # Never return early on a missing title: the disable already happened, and the
    # admin-notification path must still run. Fall back to the chat id for display
    # so admins are still notified about deactivation (PR review finding).
    display_title = title or str(chat_id)

    logger.info(
        f"Moderation disabled for {format_chat_log(chat_id, display_title, username)}"
    )

    try:
        admins = await bot.get_chat_administrators(chat_id)
    except Exception:  # noqa: BLE001
        # Chat fully unreachable (e.g. bot removed): nothing to notify, but the
        # disable already happened — log and stop gracefully, not a crash.
        logger.warning(
            f"Failed to list admins for {format_chat_log(chat_id, display_title, username)} "
            "during deactivation — skipping admin notifications"
        )
        return

    min_credits_admin, min_credits = await find_min_credits_admin(admins)

    if min_credits_admin:
        logger.info(
            f"Min-credits admin {min_credits_admin.user.id} (balance={min_credits}) for {format_chat_log(chat_id, display_title, username)}"
        )
        bot_info = await bot.me()
        ref_link = f"https://t.me/{bot_info.username}?start={min_credits_admin.user.id}"

        await send_group_deactivation_message(
            chat_id, ref_link, min_credits_admin, min_credits
        )

        # Build per-admin message data for the shared notification handler
        human_admin_ids = [
            a.user.id
            for a in admins
            if isinstance(a, (ChatMemberAdministrator, ChatMemberOwner))
            and not a.user.is_bot
        ]

        # Pre-resolve admin languages and group display names
        default_lang = "en"
        admin_objs = await asyncio.gather(*(get_admin(aid) for aid in human_admin_ids))
        admin_langs: dict[int, str] = {}
        for aid, admin_obj in zip(human_admin_ids, admin_objs):
            admin_langs[aid] = (
                normalize_lang(admin_obj.language_code)
                if admin_obj and admin_obj.language_code
                else default_lang
            )

        group_displays: dict[str, str] = {}
        for lang_code in set(admin_langs.values()) | {default_lang}:
            group_displays[lang_code] = format_chat_or_channel_display(
                title, username, t(lang_code, "common.group")
            )

        def _deactivation_message(admin_id: int) -> str:
            lang = admin_langs.get(admin_id, default_lang)
            group_display = group_displays.get(lang, group_displays[default_lang])
            msg = t(lang, "deactivate.admin_message", group=group_display)
            msg += t(lang, "deactivate.admin_invite", ref_link=ref_link)
            return msg

        notify_result = await notify_admins_with_fallback_and_cleanup(
            bot,
            human_admin_ids,
            chat_id,
            private_message=_deactivation_message,
            group_message_template="{mention}, "
            + t(default_lang, "deactivate.group_fallback_message"),
            cleanup_if_group_fails=False,
            assume_human_admins=True,
        )
        logger.info(
            f"Deactivation notification for {format_chat_log(chat_id, title, username)}: "
            f"{len(notify_result.get('notified_private', []))} admins notified in private, "
            f"{len(notify_result.get('unreachable', []))} unreachable"
        )
    else:
        logger.info(
            f"No min-credits admin found for {format_chat_log(chat_id, title, username)} — nothing to notify"
        )


async def find_min_credits_admin(
    admins: Sequence[ChatMember],
) -> tuple[ChatMemberAdministrator | ChatMemberOwner | None, float]:
    """
    Находит администратора с наименьшим количеством звезд.

    Args:
        admins: Список администраторов

    Returns:
        Tuple[Optional[Union[ChatMemberAdministrator, ChatMemberOwner]], float]:
            Админ с минимальным балансом и его баланс
    """
    min_credits_admin = None
    min_credits = float("inf")

    for admin in admins:
        if not isinstance(admin, (ChatMemberAdministrator, ChatMemberOwner)):
            continue
        if admin.user.is_bot:
            continue
        admin_data = await get_admin(admin.user.id)
        if admin_data and admin_data.credits < min_credits:
            min_credits = admin_data.credits
            min_credits_admin = admin

    logger.debug(
        f"Min-credits admin for deactivation: {min_credits_admin.user.id if min_credits_admin else None}, balance={min_credits}"
    )
    return min_credits_admin, min_credits


@logfire.instrument(extract_args=True, record_return=True)
async def send_group_deactivation_message(
    chat_id: int,
    ref_link: str,
    min_credits_admin: ChatMemberAdministrator | ChatMemberOwner,
    min_credits: float,
) -> None:
    """
    Отправляет сообщение о деактивации в группу.

    Args:
        chat_id: ID чата
        ref_link: Реферальная ссылка
        min_credits_admin: Админ с минимальным балансом
        min_credits: Минимальный баланс
    """
    first_admin = await get_admin(min_credits_admin.user.id)
    lang = (
        normalize_lang(first_admin.language_code)
        if first_admin and first_admin.language_code
        else "en"
    )
    message_text = t(
        lang,
        "deactivate.group_message",
        add_to_group_url=get_add_to_group_url(),
    )

    # Resolve chat display context for outcome logging (guarded — the chat may
    # be inaccessible exactly when the deactivation post fails)
    title = username = None
    try:
        chat = await bot.get_chat(chat_id)
        title = getattr(chat, "title", None)
        username = getattr(chat, "username", None)
    except Exception:  # noqa: BLE001
        title = username = None

    try:

        async def send_deactivation_message():
            return await bot.send_message(
                chat_id,
                message_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        sent = await send_deactivation_message()
        logger.info(
            f"Deactivation message sent to {format_chat_log(chat_id, title, username)} (message_id={sent.message_id})"
        )

    except Exception as e:
        logger.warning(
            f"Failed to send group deactivation message to {format_chat_log(chat_id, title, username)}: {e}",
            exc_info=True,
        )
