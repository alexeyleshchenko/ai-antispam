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

from ..database.group_operations import get_paid_groups_with_moderation_off

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
