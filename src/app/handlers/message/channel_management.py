"""
Channel management and administration.

This module handles channel-related operations including notifications
to administrators when the bot is incorrectly added to channels.
"""

import contextlib
import logging

from aiogram import types
from aiogram.client.bot import Bot
from aiogram.exceptions import TelegramForbiddenError

from ...common.userbot_messaging import send_userbot_dm
from ...common.utils import format_chat_log, format_chat_or_channel_display, format_user_log, retry_on_network_error
from ...i18n import normalize_lang, t

logger = logging.getLogger(__name__)


async def handle_channel_post(message: types.Message) -> str:
    """
    Handle channel posts when bot is incorrectly added to a channel.

    When the bot is added to a channel instead of a discussion group,
    it notifies channel administrators with instructions and leaves the channel.

    Args:
        message: The channel post message

    Returns:
        Result identifier string for logging
    """
    try:
        from ...common.bot import bot

        await notify_channel_admins_and_leave(message.chat, bot)
        return "channel_post_left_channel"
    except Exception as e:
        logger.error(f"Error handling channel_post: {e}", exc_info=True)
        return "channel_post_error"


async def get_discussion_username(chat: types.Chat, bot: Bot) -> str | None:
    """
    Get the username of the linked discussion group.

    Args:
        chat: The channel chat object
        bot: The bot instance for API calls

    Returns:
        Username of discussion group if available, None otherwise
    """
    linked_chat_id = getattr(chat, "linked_chat_id", None)
    if linked_chat_id is not None:
        try:
            discussion_chat = await bot.get_chat(int(linked_chat_id))
            return getattr(discussion_chat, "username", None)
        except Exception as e:
            logger.warning(
                f"Failed to get linked discussion group {format_chat_log(linked_chat_id)}: {e}"
            )
    return None


def build_channel_instruction_message(
    channel_title: str,
    discussion_link: str | None,
    channel_username: str | None = None,
    *,
    lang: str = "en",
) -> str:
    """
    Build the instructional message for channel administrators.

    Args:
        channel_title: Title of the channel
        discussion_link: URL to the discussion group if available
        channel_username: Optional channel username without @
        lang: Language for message

    Returns:
        Formatted HTML message with instructions
    """
    channel_display = format_chat_or_channel_display(
        channel_title, channel_username, t(lang, "common.channel")
    )
    base_instruction = t(lang, "channel.wrong_place", channel=channel_display)

    if discussion_link:
        base_instruction += (
            f'<b>Discussion Group:</b> <a href="{discussion_link}">go to group</a>\n\n'
        )

    base_instruction += t(lang, "channel.more_info")

    return base_instruction


def build_channel_instruction_userbot_message(
    channel_title: str,
    discussion_link: str | None,
    channel_username: str | None = None,
    *,
    lang: str = "en",
) -> str:
    """
    Build the instructional message for userbot fallback DM.

    Used when the Bot API cannot reach the user (e.g. bot removed from channel).
    The message comes from an unknown account, so it must include context:
    who sent it, why from this account, and the actual instruction.

    Args:
        channel_title: Title of the channel
        discussion_link: URL to the discussion group if available
        channel_username: Optional channel username without @

    Returns:
        Formatted HTML message with preamble and instruction
    """
    instruction_body = build_channel_instruction_message(
        channel_title, discussion_link, channel_username, lang=lang
    )
    preamble = t(lang, "channel.userbot_preamble")
    return preamble + instruction_body


async def notify_channel_admins(
    chat: types.Chat, instruction: str, bot: Bot
) -> list[int]:
    """
    Notify all non-bot administrators of the channel.

    Args:
        chat: The channel chat object
        instruction: The message to send to administrators
        bot: The bot instance for sending messages

    Returns:
        List of admin IDs that were successfully notified
    """
    notified_admins = []

    try:
        admins = await bot.get_chat_administrators(chat.id)
    except Exception as e:
        logger.warning(
            f"Failed to get channel admins for {format_chat_log(chat.id, chat.title, getattr(chat, 'username', None))}: {e}", exc_info=True
        )
        return notified_admins

    for admin in admins:
        if admin.user.is_bot:
            continue

        admin_id = admin.user.id
        try:

            @retry_on_network_error
            async def send_instruction() -> None:
                await bot.send_message(admin_id, instruction, parse_mode="HTML")

            await send_instruction()
            notified_admins.append(admin_id)
        except Exception as e:
            logger.warning(
                f"Failed to send instruction to admin {format_user_log(admin_id, admin.user.full_name, getattr(admin.user, 'username', None))}: {e}", exc_info=True
            )

    return notified_admins


async def notify_channel_admins_and_leave(
    chat: types.Chat,
    bot: Bot,
    *,
    adding_user: types.User | None = None,
) -> None:
    """
    Notify channel administrators about incorrect bot placement and leave the channel.

    When the bot is added to a channel instead of a discussion group, this function:
    1. Creates an instructional message for administrators
    2. Attempts to find and notify all channel administrators
    3. Leaves the channel to prevent confusion

    If the primary flow fails (e.g. bot not a member, TelegramForbiddenError),
    falls back to userbot DM to the adding user when they have a username.

    Args:
        chat: The channel chat object
        bot: The bot instance for sending messages and leaving
        adding_user: Optional user who added the bot (for fallback DM when primary fails)
    """
    channel_title = chat.title or "(untitled)"
    channel_username = getattr(chat, "username", None)
    discussion_username = await get_discussion_username(chat, bot)

    discussion_link = (
        f"https://t.me/{discussion_username}" if discussion_username else None
    )

    lang = "en"
    with contextlib.suppress(Exception):
        admins = await bot.get_chat_administrators(chat.id)
        for a in admins:
            if not a.user.is_bot:
                from ...database import get_admin

                admin_obj = await get_admin(a.user.id)
                if admin_obj and admin_obj.language_code:
                    lang = normalize_lang(admin_obj.language_code)
                elif getattr(a.user, "language_code", None):
                    lang = normalize_lang(a.user.language_code)
                break
    instruction = build_channel_instruction_message(
        channel_title, discussion_link, channel_username, lang=lang
    )

    try:
        notified_admins = await notify_channel_admins(chat, instruction, bot)
        await bot.leave_chat(chat.id)
        logger.info(
            f"Bot left channel {format_chat_log(chat.id, channel_title, channel_username)} after notifying {len(notified_admins)} admins."
        )
    except TelegramForbiddenError as e:
        logger.warning(
            f"Bot API failed for channel {format_chat_log(chat.id, channel_title, channel_username)} (e.g. bot not a member): {e}"
        )
        # Fallback: userbot DM to adding user if they have username
        adding_username = (
            getattr(adding_user, "username", None) if adding_user else None
        )
        if (
            adding_user
            and adding_username
            and not getattr(adding_user, "is_bot", False)
        ):
            fallback_lang = normalize_lang(getattr(adding_user, "language_code", None))
            userbot_message = build_channel_instruction_userbot_message(
                channel_title, discussion_link, channel_username, lang=fallback_lang
            )
            success = await send_userbot_dm(
                username=adding_username,
                user_id=adding_user.id,
                message=userbot_message,
            )
            if success:
                logger.info(
                    "Sent channel instruction to adding user via userbot fallback",
                    extra={"username": adding_username, "channel_id": chat.id},
                )
            else:
                logger.warning(
                    "Userbot fallback DM failed for adding user",
                    extra={"username": adding_username, "channel_id": chat.id},
                )
