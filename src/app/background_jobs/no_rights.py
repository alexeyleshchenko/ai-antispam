"""
No-rights grace period jobs.

Two jobs, one clock (`groups.no_rights_detected_at`):

- `notify_degraded_admin_rights` fires ONCE per detection, for groups where the
  bot is still an ADMINISTRATOR but has a right withheld (#144). Those groups
  never reach the leave branch, so this is the only surface that tells their
  admins anything at all.
- `leave_no_rights_groups` runs daily and leaves the groups the bot genuinely
  cannot act in (not an administrator, kicked, or unreadable) after the grace
  period, notifying the admins on the way out.

The split is #144's: leaving a degraded administrator destroys `approved_members`
and `group_administrators` irreversibly, over one unticked checkbox in
Telegram's own UI, while the bot still receives every update and can be
repaired by a single permission change.
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
    get_discussion_group_id,
    get_groups_with_no_rights_past_grace,
    get_protected_channel_ids,
    pause_group_preserving_data,
)
from ..i18n import resolve_lang, t

logger = logging.getLogger(__name__)


async def notify_degraded_admin_rights(degraded: list[dict]) -> int:
    """Tell the admins of a degraded-administrator group, once, at detection (#144).

    `degraded` is the sweep's `rights_degraded` payload: the groups whose clock
    went `NULL → NOW()` on THIS sweep while the bot was an administrator with a
    right withheld. One entry per detection, so this is one-shot by
    construction — the sweep only reports the transition, never the state.

    Why it cannot ride the leave job: `admin_to_left_groups` is populated inside
    the leave branch (`_leave_group_and_record_for_notification`), so a class
    that stays would be notified about nothing. That is the whole reason this
    function exists.

    The message NAMES the missing right in Telegram's own words ("Delete
    messages", "Ban users"): the admin has to tick a specific checkbox, and
    "we lost rights" is not actionable. HQ's constraint, 2026-11-09.

    Returns the number of admins actually messaged.
    """
    if not degraded:
        return 0

    sent = 0
    for entry in degraded:
        group_id = entry["group_id"]
        group = await get_group(group_id)
        admin_ids = list(group.admin_ids) if group and group.admin_ids else []
        if not admin_ids:
            logger.warning(
                f"No-rights notify: {format_chat_log(group_id, entry.get('title'))} "
                "is a degraded administrator with no known admin to tell"
            )
            continue

        display = format_chat_or_channel_display(
            entry.get("title") or str(group_id), None, t("en", "common.group")
        )
        for admin_id in admin_ids:
            admin = await get_admin(admin_id)
            if admin and not admin.is_active:
                continue
            lang = resolve_lang(None, admin)
            text = t(
                lang,
                "no_rights.degraded_rights",
                group=display,
                missing=entry.get("missing") or t(lang, "no_rights.some_rights"),
                add_to_group_url=get_add_to_group_url(),
            )
            if await send_admin_dm(admin_id, text, log_context="no-rights-degraded"):
                sent += 1
        logger.info(
            f"No-rights notify: told {len(admin_ids)} admin(s) of "
            f"{format_chat_log(group_id, entry.get('title'))} that the bot is an "
            f"administrator without {entry.get('missing')}"
        )
    return sent


async def _leave_group_and_record_for_notification(
    group_id: int,
    admin_to_left_groups: dict[int, list[tuple[int, str]]],
) -> bool:
    """Leave group and record admins for notification. Returns True if left successfully."""
    group = await get_group(group_id)
    title = username = None
    try:
        chat = await bot.get_chat(group_id)
        title = getattr(chat, "title", None) or str(group_id)
        username = getattr(chat, "username", None)
        display = format_chat_or_channel_display(
            title, username, t("en", "common.group")
        )
    except Exception:  # noqa: BLE001
        display = str(group_id)
    success = await perform_complete_group_cleanup(
        group_id, title, username, status="paused", reason="no_rights_past_grace"
    )
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

    # #145: the protected set is read ONCE per run, not per group — it is one
    # query and the set cannot change mid-run in a way that matters.
    protected_channels = set(await get_protected_channel_ids())

    # Collect admin_id -> list of (group_id, display) for groups we leave
    admin_to_left_groups: dict[int, list[tuple[int, str]]] = {}

    for group_id in group_ids:
        skip_leave = False
        title = username = None
        try:
            chat = await bot.get_chat(group_id)
            title = getattr(chat, "title", None)
            username = getattr(chat, "username", None)
        except Exception:  # noqa: BLE001
            title = username = None

        if group_id in protected_channels:
            # #145: never leave a protected channel. A channel in the protected
            # set is the linked channel of a discussion group the bot protects,
            # so a leave cascades into losing that group — the same hazard
            # `handle_channel_post` already refuses (Valeri trace / #34).
            #
            # STATUS-ONLY on purpose: `cleanup_group_data` hard-deletes
            # `group_administrators` and `approved_members`, which is precisely
            # what this hold exists to preserve (SV CHAT carries 58 approved
            # members, and a re-add restores none of them). `retire_group` is
            # equally wrong: it stamps `bot_removed_at`, false here because
            # the bot never leaves.
            discussion_group_id = await get_discussion_group_id(group_id)
            await pause_group_preserving_data(
                group_id,
                reason="no_rights_protected_channel",
            )
            # Clear the clock so tomorrow's run stops re-enqueuing it. Clearing
            # is what makes this one-shot; the row stays paused, so #138's close
            # condition (`status != 'active'`) is still satisfied.
            await clear_no_rights_detected_at(group_id)
            # One-shot by construction, not by wording: both populations filter
            # `status = 'active'` (grace job `get_groups_with_no_rights_past_grace`,
            # sweep `sweep_group_administrators`), so the pause above removes this
            # row from BOTH. The clock would be re-stamped otherwise — that was
            # the 7-day loop the status-only write exists to end.
            logger.warning(
                f"NOT leaving protected channel {format_chat_log(group_id, title, username)}: "
                f"it is the linked channel of discussion group {discussion_group_id}, "
                "whose protection would end with the leave. "
                "Status=paused, mappings preserved, clock cleared — terminal, "
                "no further sweep will revisit it"
            )
            continue

        try:
            member = await bot.get_chat_member(group_id, bot_user_id)
            if isinstance(member, ChatMemberAdministrator):
                can_delete = getattr(member, "can_delete_messages", False)
                can_restrict = getattr(member, "can_restrict_members", False)
                if can_delete and can_restrict:
                    await clear_no_rights_detected_at(group_id)
                    logger.info(
                        f"Rights restored in group {format_chat_log(group_id, title, username)}, cleared flag"
                    )
                    skip_leave = True
                else:
                    # #144: still an ADMINISTRATOR, merely degraded. It receives
                    # every update and one unticked checkbox repairs it, while
                    # leaving would destroy approved_members irreversibly. Hold
                    # the flag and stay; the detection DM already fired when the
                    # clock started, so there is nothing to send here.
                    withheld = ", ".join(
                        name
                        for name, granted in (
                            ("can_delete_messages", can_delete),
                            ("can_restrict_members", can_restrict),
                        )
                        if not granted
                    )
                    logger.info(
                        f"Holding group {format_chat_log(group_id, title, username)}: "
                        f"bot is an administrator without {withheld} — not leaving "
                        "(repairable by permission change; flag kept)"
                    )
                    skip_leave = True
        except Exception as e:
            if is_group_inaccessible_error(e):
                logger.info(
                    f"Group {format_chat_log(group_id, title, username)} already inaccessible during rights check, "
                    "proceeding with stale cleanup"
                )
            else:
                logger.warning(
                    f"Error checking rights in group {format_chat_log(group_id, title, username)}: {e}",
                    exc_info=True,
                )

        if not skip_leave:
            try:
                success = await _leave_group_and_record_for_notification(
                    group_id, admin_to_left_groups
                )
                if success:
                    logger.info(
                        f"Left no-rights group {format_chat_log(group_id, title, username)}"
                    )
                else:
                    logger.warning(
                        f"Failed to leave group {format_chat_log(group_id, title, username)} (unexpected cleanup failure)"
                    )
            except Exception as cleanup_e:  # noqa: BLE001
                logger.warning(
                    f"Cleanup failed for {format_chat_log(group_id, title, username)}: {cleanup_e}"
                )

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
