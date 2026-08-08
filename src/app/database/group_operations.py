import logging
from typing import cast

from aiogram.exceptions import TelegramBadRequest

from ..common.bot import bot
from ..common.telegram_errors import GROUP_ANONYMOUS_BOT_ID, is_group_inaccessible_error
from ..common.utils import format_chat_log, load_config
from . import admin_operations
from .models import Group
from .postgres_connection import get_pool

logger = logging.getLogger(__name__)


async def get_group(group_id: int) -> Group | None:
    """Retrieve group information"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        group_data = await conn.fetchrow(
            """
            SELECT * FROM groups WHERE group_id = $1
        """,
            group_id,
        )

        if not group_data:
            return None

        admin_ids = [
            row["admin_id"]
            for row in await conn.fetch(
                """
            SELECT admin_id FROM group_administrators WHERE group_id = $1
        """,
                group_id,
            )
        ]

        member_ids = [
            row["member_id"]
            for row in await conn.fetch(
                """
            SELECT member_id FROM approved_members WHERE group_id = $1
        """,
                group_id,
            )
        ]

        return Group(
            group_id=group_id,
            admin_ids=admin_ids,
            moderation_enabled=group_data["moderation_enabled"],
            member_ids=member_ids,
            title=group_data.get("title"),
            username=group_data.get("username"),
            created_at=group_data["created_at"],
            last_updated=group_data["last_active"],
        )


async def set_group_moderation(group_id: int, enabled: bool) -> None:
    """Enable/disable moderation for a group"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO groups (group_id, moderation_enabled, last_active)
            VALUES ($1, $2, NOW())
            ON CONFLICT (group_id) DO UPDATE
            SET moderation_enabled = $2, last_active = NOW()
        """,
            group_id,
            enabled,
        )


async def is_moderation_enabled(group_id: int) -> bool:
    """Check if moderation is enabled for a group"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        enabled = await conn.fetchval(
            """
            SELECT moderation_enabled FROM groups WHERE group_id = $1
        """,
            group_id,
        )
        return bool(enabled)


async def get_paying_admins(group_id: int) -> list[int]:
    """Get list of admins with positive credits"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT a.admin_id
            FROM administrators a
            JOIN group_administrators ga ON a.admin_id = ga.admin_id
            WHERE ga.group_id = $1 AND a.credits > 0
        """,
            group_id,
        )
        return [row["admin_id"] for row in rows]


async def deduct_credits_from_admins(group_id: int, amount: int) -> int:
    """
    Deduct credits from the admin with the highest balance
    Returns:
        int: admin_id if credits were successfully deducted, 0 if deduction failed
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # Find admin with highest balance
        admin_row = await conn.fetchrow(
            """
                SELECT a.admin_id, a.credits
                FROM administrators a
                JOIN group_administrators ga ON a.admin_id = ga.admin_id
                WHERE ga.group_id = $1
                ORDER BY a.credits DESC
                LIMIT 1
            """,
            group_id,
        )

        if not admin_row or admin_row["credits"] < amount:
            return 0

        # Deduct credits and record transaction
        await conn.execute(
            """
                UPDATE administrators
                SET credits = credits - $1, last_active = NOW(),
                    credits_depleted_at = CASE
                        WHEN credits - $2 = 0 AND credits_depleted_at IS NULL
                        THEN NOW() ELSE credits_depleted_at END
                WHERE admin_id = $3
            """,
            amount,
            amount,
            admin_row["admin_id"],
        )

        await conn.execute(
            """
                INSERT INTO transactions (admin_id, amount, type, description)
                VALUES ($1, $2, 'deduct', 'Group moderation credit deduction')
            """,
            admin_row["admin_id"],
            -amount,
        )

        return admin_row["admin_id"]


async def cleanup_group_data(group_id: int) -> None:
    """Clean up all database records for a group"""
    logger.info(f"Cleaning up database records for group {format_chat_log(group_id)}")

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Remove all admin associations
        await conn.execute(
            """
            DELETE FROM group_administrators
            WHERE group_id = $1
            """,
            group_id,
        )

        # Remove approved members
        await conn.execute(
            """
            DELETE FROM approved_members
            WHERE group_id = $1
            """,
            group_id,
        )

        # Remove the group itself
        await conn.execute(
            """
            DELETE FROM groups
            WHERE group_id = $1
            """,
            group_id,
        )

    logger.info(f"Successfully cleaned up database records for group {format_chat_log(group_id)}")


async def set_no_rights_detected_at(group_id: int) -> None:
    """Set no_rights_detected_at = NOW() only if currently NULL (first detection)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE groups
            SET no_rights_detected_at = NOW()
            WHERE group_id = $1 AND no_rights_detected_at IS NULL
            """,
            group_id,
        )


async def clear_no_rights_detected_at(group_id: int) -> None:
    """Clear no_rights_detected_at when rights are restored."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE groups
            SET no_rights_detected_at = NULL
            WHERE group_id = $1
            """,
            group_id,
        )


async def get_groups_with_no_rights_past_grace(grace_days: int) -> list[int]:
    """Return group IDs where no_rights_detected_at is set and past grace period."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT group_id
            FROM groups
            WHERE no_rights_detected_at IS NOT NULL
              AND no_rights_detected_at + make_interval(days => $1) <= NOW()
            """,
            grace_days,
        )
        return [row["group_id"] for row in rows]


async def get_admin_group_ids(admin_id: int) -> list[int]:
    """Get list of group IDs where admin is a member (DB only, no Telegram API)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT group_id FROM group_administrators WHERE admin_id = $1
            """,
            admin_id,
        )
        return [row["group_id"] for row in rows]


async def get_admin_groups(admin_id: int) -> list[dict]:
    """Get list of groups where user is an admin"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT g.group_id, g.title, g.moderation_enabled
            FROM groups g
            JOIN group_administrators ga ON g.group_id = ga.group_id
            WHERE ga.admin_id = $1
        """,
            admin_id,
        )

        groups = []
        inaccessible_groups = []

        for row in rows:
            try:
                chat = await bot.get_chat(row["group_id"])
                groups.append(
                    {
                        "id": row["group_id"],
                        "title": chat.title,
                        "is_moderation_enabled": row["moderation_enabled"],
                    }
                )
            except Exception as e:
                if is_group_inaccessible_error(e):
                    logger.info(
                        f"Group {row['group_id']} inaccessible during admin stats, "
                        "skipping and cleaning up stale DB record"
                    )
                    inaccessible_groups.append(row["group_id"])
                elif isinstance(e, TelegramBadRequest):
                    logger.exception(
                        f"Telegram error getting chat {row['group_id']}",
                    )
                else:
                    logger.exception(
                        f"Error getting chat {row['group_id']}",
                    )
                continue

        # Clean up inaccessible groups (after the loop to avoid connection issues)
        for group_id in inaccessible_groups:
            try:
                await cleanup_group_data(group_id)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to cleanup inaccessible group {format_chat_log(group_id)}: {e}")

        return groups


def get_probation_min_events() -> int:
    """Minimum moderated events before a member is trusted (skip LLM)."""
    return int(load_config().get("spam", {}).get("probation_min_events", 3))


async def get_moderation_event_count(group_id: int, member_id: int) -> int | None:
    """Return moderation_event_count if member is approved, else None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            SELECT moderation_event_count FROM approved_members
            WHERE group_id = $1 AND member_id = $2
            """,
            group_id,
            member_id,
        )


async def is_trusted_member(group_id: int, member_id: int) -> bool:
    """True if member is approved and has completed probation."""
    count = await get_moderation_event_count(group_id, member_id)
    return False if count is None else count >= get_probation_min_events()


async def increment_moderation_events(group_id: int, member_id: int) -> None:
    """Increment moderation event counter for an approved member."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE approved_members
            SET moderation_event_count = moderation_event_count + 1
            WHERE group_id = $1 AND member_id = $2
            """,
            group_id,
            member_id,
        )


async def set_moderation_events(group_id: int, member_id: int, count: int) -> None:
    """Set moderation event count (upsert for admin instant trust)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO approved_members (group_id, member_id, moderation_event_count)
            VALUES ($1, $2, $3)
            ON CONFLICT (group_id, member_id) DO UPDATE
            SET moderation_event_count = EXCLUDED.moderation_event_count
            """,
            group_id,
            member_id,
            count,
        )


async def is_member_in_group(group_id: int, member_id: int) -> bool:
    """Check if member is in group"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM approved_members
                WHERE group_id = $1 AND member_id = $2
            )
        """,
            group_id,
            member_id,
        )
        return bool(exists)


async def add_member(group_id: int, member_id: int) -> bool:
    """Add unique member to group with moderation_event_count=1. Returns True if inserted."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO approved_members (group_id, member_id, moderation_event_count)
            VALUES ($1, $2, 1)
            ON CONFLICT DO NOTHING
            RETURNING member_id
        """,
            group_id,
            member_id,
        )
        return row is not None


async def remove_member_from_group(
    member_id: int, group_id: int | None = None
) -> None:
    """Remove a member from a group or all groups"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        if group_id is not None:
            # Remove from specific group
            await conn.execute(
                """
                    DELETE FROM approved_members
                    WHERE group_id = $1 AND member_id = $2
                """,
                group_id,
                member_id,
            )

            await conn.execute(
                """
                    UPDATE groups SET last_active = NOW()
                    WHERE group_id = $1
                """,
                group_id,
            )
        else:
            # Remove from all groups
            groups = await conn.fetch(
                """
                    SELECT DISTINCT group_id
                    FROM approved_members
                    WHERE member_id = $1
                """,
                member_id,
            )

            await conn.execute(
                """
                    DELETE FROM approved_members WHERE member_id = $1
                """,
                member_id,
            )

            if groups:
                await conn.execute(
                    """
                        UPDATE groups SET last_active = NOW()
                        WHERE group_id = ANY($1::bigint[])
                    """,
                    [g["group_id"] for g in groups],
                )


async def update_group_admins(
    group_id: int,
    admin_ids: list[int],
    admin_usernames: list[str | None] | None = None,
    group_title: str | None = None,
    group_username: str | None = None,
) -> None:
    """Update list of group administrators with optional usernames"""
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # Ensure group exists, updating title/username when provided
        await conn.execute(
            """
            INSERT INTO groups (group_id, title, username, moderation_enabled, created_at, last_active)
            VALUES ($1, $2, $3, TRUE, NOW(), NOW())
            ON CONFLICT (group_id) DO UPDATE
            SET last_active = NOW(),
                title = COALESCE(EXCLUDED.title, groups.title),
                username = COALESCE(EXCLUDED.username, groups.username)
            """,
            group_id,
            group_title,
            group_username,
        )

        # Handle both old format (just IDs) and new format (IDs with usernames)
        usernames = cast(
            "list[str | None]",
            admin_usernames
            if admin_usernames is not None
            else [None] * len(admin_ids),
        )

        # Ensure we have usernames for all admins
        if len(usernames) != len(admin_ids):
            raise ValueError(
                "admin_ids and admin_usernames must have the same length"
            )

        # Add/update admins
        for admin_id, username in zip(admin_ids, usernames):
            # Skip GROUP_ANONYMOUS_BOT_ID — it appears in admin lists for groups with
            # anonymous admin enabled but cannot receive bot-to-bot DMs.
            if admin_id == GROUP_ANONYMOUS_BOT_ID:
                continue

            # Save or update admin with username if provided
            admin = await admin_operations.get_admin(admin_id)
            if admin is None:
                # Create new admin
                admin = admin_operations.Administrator(
                    admin_id=admin_id,
                    username=username,
                    credits=admin_operations.INITIAL_CREDITS,
                    moderation_mode=admin_operations.ModerationMode.NOTIFY,
                )
            elif admin.username is None and username is not None:
                admin.username = username

            await admin_operations.save_admin(admin)

            # Add as group administrator
            await conn.execute(
                """
                INSERT INTO group_administrators (group_id, admin_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                group_id,
                admin_id,
            )


async def upsert_awaiting_rights_group(
    group_id: int,
    group_title: str | None = None,
    group_username: str | None = None,
    linked_channel_id: int | None = None,
) -> None:
    """Upsert a group row as known-but-inactive (awaiting admin rights).

    Used when Telegram auto-adds the bot to a channel's linked discussion group:
    the bot is a plain member with no moderation rights yet. The row is registered
    with moderation_enabled=false so the group is tracked but not moderated until
    the owner promotes the bot to admin; promotion flips it active via the normal
    permission-update path. Idempotent: re-running (auto-add before/after the
    channel handler) never errors and never resets an active group.

    Args:
        group_id: The discussion group id
        group_title: Title of the discussion group
        group_username: Username of the discussion group, if public
        linked_channel_id: The channel this discussion group is linked to (used by
            the channel_post guard to detect protected channels)
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO groups (group_id, title, username, moderation_enabled, linked_channel_id, created_at, last_active)
            VALUES ($1, $2, $3, FALSE, $4, NOW(), NOW())
            ON CONFLICT (group_id) DO UPDATE
            SET last_active = NOW(),
                title = COALESCE(EXCLUDED.title, groups.title),
                username = COALESCE(EXCLUDED.username, groups.username),
                linked_channel_id = COALESCE(EXCLUDED.linked_channel_id, groups.linked_channel_id)
            """,
            group_id,
            group_title,
            group_username,
            linked_channel_id,
        )


async def activate_discussion_group(group_id: int) -> bool:
    """Flip an awaiting-rights discussion group to active moderation.

    Scoped activation for the channel-protect flow (issue #34/#35): when the
    owner promotes the bot to admin in the linked discussion group, the
    awaiting-rights row (moderation_enabled=FALSE, linked_channel_id set) must
    become active — otherwise the bot registers the group, tells the owner to
    promote it, and then never moderates (validation returns
    message_moderation_disabled forever).

    Deliberately NOT a blanket `moderation_enabled = TRUE` on the upsert's
    ON CONFLICT: that would clobber groups where the owner explicitly disabled
    moderation (they have no linked_channel_id). This function only touches rows
    registered via the awaiting-rights path, i.e. linked_channel_id IS NOT NULL
    AND moderation_enabled = FALSE. Idempotent and safe to call repeatedly.

    Args:
        group_id: The discussion group id to activate

    Returns:
        True if the group is a linked-channel discussion group (flipped from
        awaiting-rights, or already active), False if it is not a linked-channel
        discussion group
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        updated = await conn.fetchval(
            """
                UPDATE groups
                SET moderation_enabled = TRUE, last_active = NOW()
                WHERE group_id = $1
                  AND linked_channel_id IS NOT NULL
                RETURNING 1
                """,
            group_id,
        )
        return updated is not None


async def get_protected_channel_ids() -> list[int]:
    """Return distinct channel ids whose discussion groups the bot protects.

    A group row with `linked_channel_id` set means the bot was (auto-)added to
    the linked discussion group of that channel and registered it for protection
    (moderation_enabled may be false while awaiting admin rights). Used to seed
    the in-memory protected-channel set for the channel_post guard.
    """
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT DISTINCT linked_channel_id FROM groups "
        "WHERE linked_channel_id IS NOT NULL"
    )
    return [row["linked_channel_id"] for row in rows]
