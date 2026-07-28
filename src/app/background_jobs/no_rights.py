"""
No-rights grace period jobs.

Scheduled daily. Leaves groups where bot has no required rights (delete_messages,
restrict_members) after grace period. Notifies admins before leaving.
"""

import logging

from aiogram.types import ChatMemberAdministrator

from ..common.bot import bot
from ..common.notifications import perform_complete_group_cleanup
from ..common.telegram_errors import is_group_inaccessible_error
from ..common.utils import (
    format_chat_log,
    format_chat_or_channel_display,
    format_user_log,
    get_add_to_group_url,
    load_config,
    send_admin_dm,
)
from ..database import get_admin, get_group
from ..database.group_operations import (
    clear_no_rights_detected_at,
    get_groups_with_no_rights_past_grace,
)
from ..i18n import resolve_lang, t

logger = logging.getLogger(__name__)


async def _leave_group_and_record_for_notification(
    group_id: int,
    admin_to_left_groups: dict[int, list[tuple[int, str]]],
) -> bool:
    """Leave group and record admins for notification. Returns True if left successfully."""
    group = await get_group(group_id)
    try:
        chat = await bot.get_chat(group_id)
        title = getattr(chat, "title", None) or str(group_id)
        username = getattr(chat, "username", None)
        display = format_chat_or_channel_display(
            title, username, t("en", "common.group")
        )
    except Exception:
        display = str(group_id)
    success = await perform_complete_group_cleanup(group_id, title, username)
    if success and group and group.admin_ids:
        for admin_id in group.admin_ids:
            if admin_id not in admin_to_left_groups:
                admin_to_left_groups[admin_id] = []
            admin_to_left_groups[admin_id].append((group_id, display))
    return success


async def leave_no_rights_groups() -> None:
    """
    Leave groups where bot has no required rights past grace period.
    Re-verify via get_chat_member before leaving. Notify admins.
    """
    grace_days = load_config().get("billing", {}).get("no_rights_grace_days", 7)
    group_ids = await get_groups_with_no_rights_past_grace(grace_days)

    if not group_ids:
        return

    bot_info = await bot.me()
    bot_user_id = bot_info.id

    # Collect admin_id -> list of (group_id, display) for groups we leave
    admin_to_left_groups: dict[int, list[tuple[int, str]]] = {}

    for group_id in group_ids:
        skip_leave = False
        try:
            member = await bot.get_chat_member(group_id, bot_user_id)
            if isinstance(member, ChatMemberAdministrator) and (
                getattr(member, "can_delete_messages", False)
                and getattr(member, "can_restrict_members", False)
            ):
                await clear_no_rights_detected_at(group_id)
                logger.info(f"Rights restored in group {format_chat_log(group_id)}, cleared flag")
                skip_leave = True
        except Exception as e:
            if is_group_inaccessible_error(e):
                logger.info(
                    f"Group {format_chat_log(group_id)} already inaccessible during rights check, "
                    "proceeding with stale cleanup"
                )
            else:
                logger.warning(
                    f"Error checking rights in group {format_chat_log(group_id)}: {e}",
                    exc_info=True,
                )

        if not skip_leave:
            try:
                success = await _leave_group_and_record_for_notification(
                    group_id, admin_to_left_groups
                )
                if success:
                    logger.info(f"Left no-rights group {format_chat_log(group_id)}")
                else:
                    logger.warning(
                        f"Failed to leave group {format_chat_log(group_id)} (unexpected cleanup failure)"
                    )
            except Exception as cleanup_e:
                logger.warning(f"Cleanup failed for {format_chat_log(group_id)}: {cleanup_e}")

    # Notify each admin about their left groups
    for admin_id, groups_list in admin_to_left_groups.items():
        admin = await get_admin(admin_id)
        if admin and not admin.is_active:
            continue

        lang = resolve_lang(None, admin)
        groups_display = "\n• ".join(display for _, display in groups_list)
        ref_link = f"https://t.me/{bot_info.username}?start={admin_id}"

        text = t(
            lang,
            "no_rights.left_groups",
            groups=groups_display,
            ref_link=ref_link,
            add_to_group_url=get_add_to_group_url(),
        )
        await send_admin_dm(admin_id, text, log_context="no-rights")
        logger.info(
            f"Notified admin {format_user_log(admin_id)} about {len(groups_list)} no-rights leaves"
        )
