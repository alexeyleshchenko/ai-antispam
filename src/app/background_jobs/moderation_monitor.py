"""Silent-unmoderation monitor (issue #41).

A group can sit at `status=active` with `moderation_enabled=false` while a
paying admin is attached. Every outward signal then reads healthy — the bot is
in the chat, its rights are intact, the balance is topped up — while
`validation` drops every message at the gate and no credit is ever deducted.

Nothing reported that state, so a paying customer's two groups were silently
unmoderated for ~36 days and the customer found it before we did. This runs
daily from `scheduled_tasks.run_scheduled_jobs` and reports it.
"""

import logging

from ..database.group_operations import (
    get_moderated_groups_without_bot_rights,
    get_paid_groups_with_moderation_off,
)

logger = logging.getLogger(__name__)


async def check_unmoderated_paid_groups() -> list[dict]:
    """Report active groups whose moderation is off while a paying admin is attached.

    Returns the offending rows (empty when healthy). A non-empty result is a
    defect in the data, not a transient: it means a paying customer is being
    silently unmoderated — exactly the state issue #41 left behind.

    Deliberately reports and does NOT auto-repair. The same flag is false by
    design while a group awaits admin rights, so a blind repair here would
    re-create the defect in a second writer; the re-add path now owns the fix,
    and this surface exists to notice when it is not enough.
    """
    groups = await get_paid_groups_with_moderation_off()
    if not groups:
        logger.info("Moderation monitor: no paying group is silently unmoderated")
        return []

    for group in groups:
        title = group["title"]
        logger.error(
            f"Moderation monitor: group {group['group_id']} ('{title}') is ACTIVE "
            f"with moderation disabled while {group['paying_admin_count']} paying "
            f"admin(s) are attached (max credits {group['max_credits']}) — every "
            "message is being dropped at validation"
        )
    logger.error(
        f"Moderation monitor: {len(groups)} paying group(s) silently unmoderated"
    )
    return groups


async def check_moderated_groups_without_bot_rights() -> list[dict]:
    """Report groups advertised as moderated that the bot cannot moderate (#143).

    The inverse failure to the one above, and the harder one to notice: the
    group reads `status='active'`, `moderation_enabled=true`, so nothing about
    its outward state suggests a problem — but the bot's own membership lacks
    `can_delete_messages` / `can_restrict_members`, so no verdict can ever be
    enforced and no credit is ever deducted. Measured 2026-11-09: 3 of 43,
    one of them an `administrator` with both rights false.

    Reads the clock the sweep sets, so it describes the last sweep rather than
    the API's availability right now. Deliberately reports and does NOT repair:
    the grace job (`leave_no_rights_groups`) owns the pause, and a second
    writer of `status` here is exactly what #141's ruling rejected.
    """
    groups = await get_moderated_groups_without_bot_rights()
    if not groups:
        logger.info("Moderation monitor: every moderated group's bot holds its rights")
        return []

    paying = [group for group in groups if group["has_paying_admin"]]
    for group in groups:
        logger.error(
            f"Moderation monitor: group {group['group_id']} "
            f"('{group['title']}') is ACTIVE with moderation enabled but the "
            "bot cannot delete or restrict — flagged "
            f"{group['no_rights_detected_at']}"
            + (", paying admin attached" if group["has_paying_admin"] else "")
        )
    logger.error(
        f"Moderation monitor: {len(groups)} moderated group(s) the bot cannot "
        f"moderate ({len(paying)} with a paying admin) — grace job owns the pause"
    )
    return groups
