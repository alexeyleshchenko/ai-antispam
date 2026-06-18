"""
Модуль для управления кредитами и деактивацией групп.

Содержит функции для:
- Списания кредитов с администраторов групп
- Деактивации модерации при недостатке кредитов
- Уведомления администраторов о деактивации
- Поиска администраторов с минимальным количеством кредитов
"""

import logging
from typing import Optional, Sequence, Tuple, Union

from aiogram.types import ChatMember, ChatMemberAdministrator, ChatMemberOwner
from aiogram.exceptions import TelegramForbiddenError

from ..common.bot import bot
from ..common.utils import (
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
        logger.warning(f"No paying admins in chat {chat_id} for {reason}")
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
        logger.warning(f"Failed to get chat title for {chat_id}")
        return

    admins = await bot.get_chat_administrators(chat_id)
    min_credits_admin, min_credits = await find_min_credits_admin(admins)

    if min_credits_admin:
        bot_info = await bot.me()
        ref_link = f"https://t.me/{bot_info.username}?start={min_credits_admin.user.id}"

        await send_group_deactivation_message(
            chat_id, ref_link, min_credits_admin, min_credits
        )
        await notify_admins_about_deactivation(
            admins, chat.title, ref_link, getattr(chat, "username", None)
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


async def notify_admins_about_deactivation(
    admins: Sequence[ChatMember],
    chat_title: str,
    ref_link: str,
    chat_username: Optional[str] = None,
) -> None:
    """
    Отправляет персональные уведомления администраторам о деактивации.

    Args:
        admins: Список администраторов
        chat_title: Название чата
        ref_link: Реферальная ссылка
        chat_username: Опциональный username группы без @
    """
    first_admin_id = next(
        (
            a.user.id
            for a in admins
            if isinstance(a, (ChatMemberAdministrator, ChatMemberOwner))
            and not a.user.is_bot
        ),
        None,
    )
    lang = "en"
    if first_admin_id:
        first_admin = await get_admin(first_admin_id)
        lang = (
            normalize_lang(first_admin.language_code)
            if first_admin and first_admin.language_code
            else "en"
        )

    group_display = format_chat_or_channel_display(
        chat_title, chat_username, t(lang, "common.group")
    )
    for admin in admins:
        if not isinstance(admin, (ChatMemberAdministrator, ChatMemberOwner)):
            continue
        if admin.user.is_bot:
            continue

        admin_id = admin.user.id
        admin_obj = await get_admin(admin_id)
        admin_lang = (
            normalize_lang(admin_obj.language_code)
            if admin_obj and admin_obj.language_code
            else lang
        )
        message_text = t(admin_lang, "deactivate.admin_message", group=group_display)
        message_text += t(admin_lang, "deactivate.admin_invite", ref_link=ref_link)

        try:

            @retry_on_network_error
            async def send_notification():
                return await bot.send_message(
                    admin_id,
                    message_text,
                    parse_mode="HTML",
                    disable_web_page_preview=True,
                )

            await send_notification()
        except TelegramForbiddenError as e:
            # Admin never started a conversation with the bot or blocked it.
            # Expected, non-critical — log without traceback.
            logger.warning(
                f"Cannot notify admin {admin_id} about deactivation: {e}. "
                "Admin should send /start to the bot to enable notifications."
            )
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin_id}: {e}", exc_info=True)
