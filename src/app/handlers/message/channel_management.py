"""
Channel management and administration.

This module handles channel-related operations including notifications
to administrators when the bot is incorrectly added to channels.
"""

import asyncio
import contextlib
import logging

import logfire
from aiogram import types
from aiogram.client.bot import Bot
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from ...common.userbot_messaging import send_userbot_dm
from ...common.utils import (
    format_chat_log,
    format_chat_or_channel_display,
    format_user_log,
    retry_on_network_error,
)
from ...i18n import normalize_lang, t

logger = logging.getLogger(__name__)

# Channel ids whose linked discussion groups the bot is actively protecting.
# Seeded at startup from the DB; protects against a channel_post self-leave
# when the bot is correctly placed (protect mode) rather than wrongly added.
_protected_channel_ids: set[int] = set()

# True when startup seeding failed (e.g. DB down at boot). While set, the
# in-memory set may be empty/stale — handle_channel_post must refuse to leave
# (leaving cascades into evicting the bot from protected discussion groups)
# until the DB positively confirms the channel is not protected.
_seed_failed: bool = False


class ProtectedChannelCheckUnavailable(Exception):
    """Raised when the protected-channel state cannot be confirmed (DB down)."""


async def _seed_protected_channels() -> None:
    """Load protected channel ids from the DB at startup."""
    global _seed_failed
    try:
        from ...database.group_operations import get_protected_channel_ids

        protected = await get_protected_channel_ids()
        _protected_channel_ids.clear()
        _protected_channel_ids.update(protected)
        _seed_failed = False
        logger.info(f"Seeded {len(protected)} protected channel(s) from DB")
    except Exception as e:
        _seed_failed = True
        logger.warning(f"Failed to seed protected channels: {e}", exc_info=True)


async def _is_protected_channel(chat_id: int) -> bool:
    """True when the channel has a discussion group the bot protects.

    In-memory set first (cheap); on a miss, re-check the DB in case the set was
    seeded before this channel was registered (e.g. a fresh protect registration
    after startup).

    Raises:
        ProtectedChannelCheckUnavailable: when the DB cannot confirm either way
            (connection error). Callers MUST NOT treat this as "not protected" —
            leaving on an unavailable DB is how a transient outage cascades into
            mass self-leave of protected channels.
    """
    if chat_id in _protected_channel_ids:
        return True
    try:
        from ...database.group_operations import get_protected_channel_ids

        if chat_id in await get_protected_channel_ids():
            _protected_channel_ids.add(chat_id)
            return True
        return False
    except ProtectedChannelCheckUnavailable:
        raise
    except Exception as e:
        logger.debug(f"Protected-channel DB re-check failed for {chat_id}: {e}")
        raise ProtectedChannelCheckUnavailable(
            f"DB unavailable while checking protected status of channel {chat_id}"
        ) from e


async def handle_channel_post(message: types.Message) -> str:
    """
    Handle channel posts when bot is incorrectly added to a channel.

    When the bot is added to a channel instead of a discussion group,
    it notifies channel administrators with instructions and leaves the channel.

    If the channel is linked to a discussion group the bot is actively
    protecting (protect mode), the post is ignored — the bot must NOT leave,
    because leaving the channel would cascade into leaving the discussion group
    and end protection (Valeri trace, issue #34).

    Args:
        message: The channel post message

    Returns:
        Result identifier string for logging
    """
    try:
        try:
            if await _is_protected_channel(message.chat.id):
                return "channel_post_ignored_protected"
        except ProtectedChannelCheckUnavailable as e:
            # DB cannot confirm whether this channel is protected. Leaving here
            # would cascade into evicting the bot from protected discussion
            # groups (Valeri trace) on a transient outage — refuse instead. The
            # post is skipped; the next channel_post (or a manual check) retries.
            logger.error(
                f"Skipping channel_post for {format_chat_log(message.chat.id, getattr(message.chat, 'title', None), getattr(message.chat, 'username', None))}: "
                f"protected-channel state unavailable ({e}). Refusing to leave — "
                f"leaving could end protection of linked discussion groups."
            )
            return "channel_post_skipped_db_unavailable"

        from ...common.bot import bot

        await notify_channel_admins_and_leave(message.chat, bot)
        return "channel_post_left_channel"
    except Exception:
        logger.exception("Error handling channel_post")
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
        except Exception as e:  # noqa: BLE001
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


_FORBIDDEN_RETRY_ATTEMPTS = 5
_FORBIDDEN_RETRY_INTERVAL = 1.0

# Exceptions retried by _with_forbidden_retry: TelegramForbiddenError (transient
# during the add-propagation window — the update arrives BEFORE the API session
# sees the bot as a member) plus the same transport errors retry_on_network_error
# handles (utils.py deliberately excludes Forbidden from ITS retry set as
# "permanent" — correct for steady-state calls, wrong for the add window).
_RETRYABLE_FORBIDDEN_AND_TRANSPORT = (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramServerError,
    TelegramRetryAfter,
    OSError,
    ConnectionError,
    TimeoutError,
)


async def _with_forbidden_retry(
    coro_factory,
    *,
    attempts: int = _FORBIDDEN_RETRY_ATTEMPTS,
    interval: float = _FORBIDDEN_RETRY_INTERVAL,
):
    """Run coro_factory, retrying Forbidden + transient errors (5×1s window).

    Telegram is eventually consistent: a `my_chat_member` update is delivered
    BEFORE the bot's API session sees it as a member (observed 2026-08-08:
    `Forbidden: bot is not a member of the channel chat` on every call 0.4s
    after an add). Within that propagation window Forbidden is TRANSIENT — this
    helper retries it like a network error, mirroring the 5×1s poll window of
    _poll_discussion_membership. After the window a Forbidden is genuinely
    permanent and surfaces to the caller's except.

    Args:
        coro_factory: zero-arg callable returning an awaitable to run
        attempts: Max attempts
        interval: Seconds between attempts

    Returns:
        The coroutine's result.

    Raises:
        The last exception once attempts are exhausted.
    """
    last_exc: BaseException | None = None
    for _ in range(attempts):
        try:
            return await coro_factory()
        except _RETRYABLE_FORBIDDEN_AND_TRANSPORT as exc:
            last_exc = exc
            await asyncio.sleep(interval)
    if last_exc is not None:
        raise last_exc


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
        admins = await _with_forbidden_retry(
            lambda: bot.get_chat_administrators(chat.id)
        )
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
            async def send_instruction(admin_id=admin_id) -> None:
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
    finding, issue #34) — it only exists on `getChat` responses. NOTE: the old
    assumption that "the bot is still a member of the channel at this point, so
    `bot.get_chat()` succeeds" is FALSE — Telegram is eventually consistent and
    delivers the update BEFORE the API session sees the bot as a member
    (observed 2026-08-08: `Forbidden: bot is not a member` on every API call
    0.4s after the add). The caller must tolerate Forbidden/transient failures
    within the propagation window (settle window + retry).

    Args:
        chat: The channel chat object (from the update payload)
        bot: The bot instance for API calls

    Returns:
        Linked discussion group id, or None if the channel has no discussion group
    """
    try:
        fresh_chat = await _with_forbidden_retry(lambda: bot.get_chat(chat.id))
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
        except Exception:  # noqa: BLE001, S110
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
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"Failed to fetch discussion group {format_chat_log(discussion_id)}: {e}"
        )

    from ...database import upsert_awaiting_rights_group

    await upsert_awaiting_rights_group(
        discussion_id,
        discussion_title,
        discussion_username,
        linked_channel_id=chat.id,
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
    except Exception:  # noqa: BLE001, S110
        pass

    for target_id in dict.fromkeys(target_ids):
        try:
            await bot.send_message(target_id, instruction, parse_mode="HTML")
            notified = True
            break
        except Exception:  # noqa: BLE001, S112
            continue

    if not notified:
        try:
            await bot.send_message(discussion_id, instruction, parse_mode="HTML")
            logger.info(
                f"Posted discussion_added notice into discussion group {format_chat_log(discussion_id, discussion_title, discussion_username)} (owner DM failed)"
            )
        except Exception:
            # Total notification failure: owner DM AND discussion-group post both
            # failed. The bot stays (leaving would cascade into leaving the
            # discussion group and end protection), but the dead-end must be
            # LOUD — the owner never learns the bot is awaiting rights here, so
            # the group would stay awaiting-rights forever. Record a logfire
            # span + ERROR log with full context for alerting/investigation.
            logger.exception(
                f"FAILED to notify owner or discussion group that bot protects "
                f"linked discussion {format_chat_log(discussion_id, discussion_title, discussion_username)} "
                f"of channel {format_chat_log(chat.id, channel_title, channel_username)} "
                f"(owner DM failed, discussion post failed). Bot stays but "
                f"the group will NOT be moderated until the owner is reached.",
            )
            with logfire.span(
                "discussion_added_notification_failed",
                channel_id=chat.id,
                channel_title=channel_title,
                discussion_id=discussion_id,
                discussion_title=discussion_title,
                target_admin_ids=[str(t) for t in dict.fromkeys(target_ids)],
            ):
                pass

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
        except Exception as e:  # noqa: BLE001
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
        try:
            await _with_forbidden_retry(lambda: bot.leave_chat(chat.id))
        except Exception as e:
            # leave_chat exhausted its retries — the bot could NOT leave the
            # channel. This is exactly the 2026-08-08 case: Telegram was still
            # propagating the add, every call said "not a member", and the bot
            # silently stayed as admin. Now it is LOUD: ERROR + logfire span
            # (channel_leave_failed), then the userbot fallback below still
            # attempts to reach the adding user.
            logger.exception(
                f"Failed to leave channel {format_chat_log(chat.id, channel_title, channel_username)} "
                f"after {_FORBIDDEN_RETRY_ATTEMPTS} attempts",
            )
            with logfire.span(
                "channel_leave_failed",
                channel_id=chat.id,
                channel_title=channel_title,
                channel_username=channel_username,
                error=str(e),
            ):
                pass
            raise
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
    except Exception as e:  # noqa: BLE001
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
