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
from typing import Optional, Sequence, Tuple, Union

from aiogram.types import ChatMember, ChatMemberAdministrator, ChatMemberOwner

from ..common.bot import bot
from ..common.notifications import notify_admins_with_fallback_and_cleanup
from ..common.utils import (
    format_chat_log,
    format_chat_or_channel_display,
    get_add_to_group_url,
    retry_on_network_error,
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
        logger.warning(f"No paying admins in chat {format_chat_log(chat_id)} for {reason}")
        await handle_deactivation(chat_id)
        return False

    return True


async def handle_deactivation(chat_id: int) -> None:
    """
    Обрабатывает деактивацию группы.

    Args:
        chat_id: ID чата
    """
    await set_group_moderation(chat_id, False)
    chat = await bot.get_chat(chat_id)
    if not chat.title:
        logger.warning(f"Failed to get chat title for {format_chat_log(chat_id)}")
        return

    admins = await bot.get_chat_administrators(chat_id)
    min_credits_admin, min_credits = await find_min_credits_admin(admins)

    if min_credits_admin:
        bot_info = await bot.me()
        ref_link = f"https://t.me/{bot_info.username}?start={min_credits_admin.user.id}"

        await send_group_deactivation_message(
            chat_id, ref_link, min_credits_admin, min_credits
        )

        # Build per-admin message data for the shared notification handler
        chat_username = getattr(chat, "username", None)
        human_admin_ids = [
            a.user.id
            for a in admins
            if isinstance(a, (ChatMemberAdministrator, ChatMemberOwner))
            and not a.user.is_bot
        ]

        # Pre-resolve admin languages and group display names
        default_lang = "en"
        admin_objs = await asyncio.gather(
            *(get_admin(aid) for aid in human_admin_ids)
        )
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
                chat.title, chat_username, t(lang_code, "common.group")
            )

        def _deactivation_message(admin_id: int) -> str:
            lang = admin_langs.get(admin_id, default_lang)
            group_display = group_displays.get(lang, group_displays[default_lang])
            msg = t(lang, "deactivate.admin_message", group=group_display)
            msg += t(lang, "deactivate.admin_invite", ref_link=ref_link)
            return msg

        await notify_admins_with_fallback_and_cleanup(
            bot,
            human_admin_ids,
            chat_id,
            private_message=_deactivation_message,
            group_message_template="{mention}, " + t(default_lang, "deactivate.group_fallback_message"),
            cleanup_if_group_fails=False,
            assume_human_admins=True,
        )


async def find_min_credits_admin(
    admins: Sequence[ChatMember],
) -> Tuple[Optional[Union[ChatMemberAdministrator, ChatMemberOwner]], float]:
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

    return min_credits_admin, min_credits


async def send_group_deactivation_message(
    chat_id: int,
    ref_link: str,
    min_credits_admin: Union[ChatMemberAdministrator, ChatMemberOwner],
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

    try:

        @retry_on_network_error
        async def send_deactivation_message():
            return await bot.send_message(
                chat_id,
                message_text,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )

        await send_deactivation_message()

    except Exception as e:
        logger.warning(f"Failed to send group promo message: {e}", exc_info=True)



