"""
Channel management and administration.

This module handles channel-related operations including notifications
to administrators when the bot is incorrectly added to channels.
"""

import asyncio
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


def build_channel_discussion_added_message(
    channel_title: str,
    discussion_link: str | None,
    discussion_title: str | None,
    channel_username: str | None = None,
    *,
    lang: str = "en",
) -> str:
    """
    Build the message for channel admins when the bot was auto-added to the
    linked discussion group and will protect it once promoted.

    Args:
        channel_title: Title of the channel
        discussion_link: URL to the discussion group if available
        discussion_title: Title of the discussion group
        channel_username: Optional channel username without @
        lang: Language for the message

    Returns:
        Formatted HTML message explaining the auto-add and promotion steps
    """
    channel_display = format_chat_or_channel_display(
        channel_title, channel_username, t(lang, "common.channel")
    )
    discussion_display = format_chat_or_channel_display(
        discussion_title or "discussion group", None, t(lang, "common.group")
    )
    base_instruction = t(
        lang,
        "channel.discussion_added",
        channel=channel_display,
        discussion=discussion_display,
    )

    if discussion_link:
        base_instruction += (
            f'<b>Discussion Group:</b> <a href="{discussion_link}">go to group</a>\n\n'
        )

    base_instruction += t(lang, "channel.more_info")

    return base_instruction


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


async def _resolve_linked_discussion_id(chat: types.Chat, bot: Bot) -> int | None:
    """Resolve the channel's linked discussion group id via an active probe.

    The `my_chat_member` update payload does NOT carry `linked_chat_id` (spike-0
    finding, issue #34) — it only exists on `getChat` responses. The bot is still
    a member of the channel at this point, so `bot.get_chat()` succeeds.

    Args:
        chat: The channel chat object (from the update payload)
        bot: The bot instance for API calls

    Returns:
        Linked discussion group id, or None if the channel has no discussion group
    """
    try:
        fresh_chat = await bot.get_chat(chat.id)
        linked_id = getattr(fresh_chat, "linked_chat_id", None)
        return int(linked_id) if linked_id is not None else None
    except Exception as e:
        logger.warning(
            f"Failed to resolve linked discussion for {format_chat_log(chat.id, chat.title, getattr(chat, 'username', None))}: {e}",
            exc_info=True,
        )
        return None


async def _poll_discussion_membership(
    discussion_id: int,
    bot: Bot,
    *,
    attempts: int = 5,
    interval: float = 1.0,
) -> bool:
    """Poll the discussion group until the bot is a member or the window expires.

    Telegram auto-adds the bot to the linked discussion group ~0.9s after the
    channel add (observed), but it is not guaranteed (0/3 spikes, issue #34). The
    5×1s window covers the auto-add case and avoids cross-restart pending state:
    the poll is synchronous inside the handler, so no partial state survives a
    restart.

    Args:
        discussion_id: The linked discussion group id
        bot: The bot instance for API calls
        attempts: Max poll attempts
        interval: Seconds between attempts

    Returns:
        True if the bot is a member/administrator/restricted in the discussion
        group within the window, False otherwise
    """
    for _ in range(attempts):
        try:
            member = await bot.get_chat_member(discussion_id, bot.id)
            if member.status in ("member", "administrator", "restricted"):
                return True
        except Exception:
            # Bot not a member yet (or transient) — keep polling
            pass
        await asyncio.sleep(interval)
    return False


async def _notify_discussion_added_and_stay(
    chat: types.Chat,
    discussion_id: int,
    discussion_username: str | None,
    bot: Bot,
    *,
    adding_user: types.User | None = None,
) -> None:
    """Notify the owner that the bot auto-added to the discussion group and stays.

    The bot is a plain member of the discussion group (Telegram auto-add, or the
    owner explicitly added it there). It cannot moderate without admin rights, so
    we register the group as awaiting-rights and tell the owner to promote the bot
    to administrator in the discussion group. We do NOT leave the channel —
    leaving would cascade into leaving the discussion group too (Valeri trace).

    If the DM to the owner fails, post the notice into the discussion group itself
    (the bot is a member there and can post).

    Args:
        chat: The channel chat object
        discussion_id: The linked discussion group id
        discussion_username: Username of the discussion group, if public
        bot: The bot instance for API calls
        adding_user: Optional user who added the bot (preferred DM target)
    """
    channel_title = chat.title or "(untitled)"
    channel_username = getattr(chat, "username", None)

    # Register the discussion group as protected-but-awaiting-rights.
    discussion_title = None
    try:
        discussion_chat = await bot.get_chat(discussion_id)
        discussion_title = getattr(discussion_chat, "title", None)
        discussion_username = discussion_username or getattr(
            discussion_chat, "username", None
        )
    except Exception as e:
        logger.warning(
            f"Failed to fetch discussion group {format_chat_log(discussion_id)}: {e}"
        )

    from ...database import upsert_awaiting_rights_group

    await upsert_awaiting_rights_group(
        discussion_id,
        discussion_title,
        discussion_username,
    )

    discussion_link = (
        f"https://t.me/{discussion_username}" if discussion_username else None
    )
    lang = "en"
    if adding_user and not getattr(adding_user, "is_bot", False):
        lang = normalize_lang(getattr(adding_user, "language_code", None))
    else:
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

    instruction = build_channel_discussion_added_message(
        channel_title,
        discussion_link,
        discussion_title,
        channel_username,
        lang=lang,
    )

    # DM the owner; on failure, post into the discussion group itself.
    notified = False
    target_ids = []
    if adding_user and not getattr(adding_user, "is_bot", False):
        target_ids.append(adding_user.id)
    try:
        admins = await bot.get_chat_administrators(chat.id)
        target_ids.extend(a.user.id for a in admins if not a.user.is_bot)
    except Exception:
        pass

    for target_id in dict.fromkeys(target_ids):
        try:
            await bot.send_message(target_id, instruction, parse_mode="HTML")
            notified = True
            break
        except Exception:
            continue

    if not notified:
        try:
            await bot.send_message(discussion_id, instruction, parse_mode="HTML")
            logger.info(
                f"Posted discussion_added notice into discussion group {format_chat_log(discussion_id, discussion_title, discussion_username)} (owner DM failed)"
            )
        except Exception as e:
            logger.warning(
                f"Failed to post discussion_added notice into discussion group {discussion_id}: {e}",
                exc_info=True,
            )

    logger.info(
        f"Bot stays in channel {format_chat_log(chat.id, channel_title, channel_username)} "
        f"protecting linked discussion group {format_chat_log(discussion_id, discussion_title, discussion_username)} (awaiting admin rights)"
    )


async def _notify_wrong_place_and_leave(
    chat: types.Chat,
    bot: Bot,
    *,
    adding_user: types.User | None = None,
    discussion_id: int | None = None,
) -> None:
    """Notify channel admins about incorrect placement and leave the channel.

    The bot was added to a channel but is NOT a member of its linked discussion
    group (or the channel has no discussion group at all). This is today's
    original behavior: build the `channel.wrong_place` instruction, notify the
    admins, leave the channel. Userbot fallback DM on TelegramForbiddenError.

    Args:
        chat: The channel chat object
        bot: The bot instance for sending messages and leaving
        adding_user: Optional user who added the bot (for fallback DM when primary fails)
        discussion_id: Optional linked discussion id (for resolving a link even
            though the bot isn't a member — best-effort)
    """
    channel_title = chat.title or "(untitled)"
    channel_username = getattr(chat, "username", None)

    discussion_username = None
    if discussion_id is not None:
        try:
            discussion_chat = await bot.get_chat(discussion_id)
            discussion_username = getattr(discussion_chat, "username", None)
        except Exception as e:
            logger.warning(
                f"Failed to resolve discussion username for {format_chat_log(discussion_id)}: {e}"
            )
    else:
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


async def notify_channel_admins_and_leave(
    chat: types.Chat,
    bot: Bot,
    *,
    adding_user: types.User | None = None,
) -> None:
    """
    Decision flow on channel-add: protect the linked discussion group, or leave.

    When the bot is added to a channel:
    1. Actively resolve the channel's linked discussion group (the update payload
       has no `linked_chat_id` — spike-0 finding; `getChat` is the only source).
    2. No discussion group → today's behavior: `channel.wrong_place` instruction
       to the admins, then leave (Alexey's requirement: "add me to the group
       instead" only when the bot is NOT in the discussion group).
    3. Discussion group exists → poll membership (auto-add may or may not have
       happened — 0/3 spikes, issue #34):
       - Bot IS a member → stay, register the discussion group as
         protected-but-awaiting-rights, and tell the owner to promote the bot to
         administrator in the discussion group (`channel.discussion_added`).
       - Bot NOT a member after the window → `channel.wrong_place` + leave.

    If the primary flow fails (e.g. bot not a member, TelegramForbiddenError),
    falls back to userbot DM to the adding user when they have a username.

    Args:
        chat: The channel chat object
        bot: The bot instance for sending messages and leaving
        adding_user: Optional user who added the bot (for fallback DM when primary fails)
    """
    discussion_id = await _resolve_linked_discussion_id(chat, bot)

    if discussion_id is None:
        # No discussion group → leave flow unchanged.
        await _notify_wrong_place_and_leave(chat, bot, adding_user=adding_user)
        return

    # Discussion group exists → resolve membership (auto-add may not have fired).
    discussion_username = None
    try:
        discussion_chat = await bot.get_chat(discussion_id)
        discussion_username = getattr(discussion_chat, "username", None)
    except Exception as e:
        logger.warning(
            f"Failed to resolve discussion group {format_chat_log(discussion_id)}: {e}"
        )

    is_member = await _poll_discussion_membership(discussion_id, bot)
    if is_member:
        await _notify_discussion_added_and_stay(
            chat,
            discussion_id,
            discussion_username,
            bot,
            adding_user=adding_user,
        )
    else:
        # Alexey's requirement: "add me to the group instead" ONLY when the bot is
        # NOT in the discussion group.
        await _notify_wrong_place_and_leave(
            chat,
            bot,
            adding_user=adding_user,
            discussion_id=discussion_id,
        )
