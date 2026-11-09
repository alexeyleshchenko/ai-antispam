import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import NamedTuple, cast

from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from ..common.bot import bot
from ..common.telegram_errors import (
    GROUP_ANONYMOUS_BOT_ID,
    is_group_inaccessible_error,
    is_member_list_inaccessible_error,
    migrate_target_id,
)
from ..common.utils import format_chat_log, load_config
from . import admin_operations
from .models import Group, GroupStatus
from .postgres_connection import get_pool
from .roster_predicates import BILLABLE_SEAT, PAYING_SEAT, REGISTERED_SEAT

logger = logging.getLogger(__name__)


def _flag(value) -> bool:
    """A DB boolean as a Python bool; NULL reads as TRUE.

    Both flags default TRUE, so NULL can only mean a row written before the
    column existed or by a path that omitted it. Reading it as TRUE preserves
    the pre-#130 behaviour in both directions: nobody loses their billing link,
    and nobody starts being moderated because a flag was unset.
    """
    return True if value is None else bool(value)


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

        # Normalize to a plain dict: asyncpg.Record and aiosqlite.Row both
        # support dict(), giving a uniform interface for the .get() calls below.
        group_data = dict(group_data)

        # One query, partitioned in Python: `get_group` runs on every message,
        # so a second round trip for the roster would be a per-message cost.
        admin_rows = await conn.fetch(
            """
            SELECT admin_id, is_registered, is_admin_now
            FROM group_administrators WHERE group_id = $1
            """,
            group_id,
        )
        admin_ids = [r["admin_id"] for r in admin_rows if _flag(r["is_registered"])]
        telegram_admin_ids = [
            r["admin_id"] for r in admin_rows if _flag(r["is_admin_now"])
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
            telegram_admin_ids=telegram_admin_ids,
            moderation_enabled=group_data["moderation_enabled"],
            member_ids=member_ids,
            status=GroupStatus(group_data.get("status", GroupStatus.ACTIVE.value)),
            title=group_data.get("title"),
            username=group_data.get("username"),
            topic_description=group_data.get("topic_description"),
            topic_description_short=group_data.get("topic_description_short"),
            topic_updated_at=group_data.get("topic_updated_at"),
            created_at=group_data["created_at"],
            last_updated=group_data["last_active"],
        )


async def set_group_moderation(
    group_id: int,
    enabled: bool,
    title: str | None = None,
    username: str | None = None,
) -> None:
    """Enable/disable moderation for a group.

    Callers that have chat metadata in hand (e.g. from bot.get_chat) should pass
    title/username so a fresh row is never created bare (no-metadata gap):
    ON CONFLICT fills metadata with COALESCE, preserving existing values when the
    caller has nothing new (title/username None).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO groups (group_id, title, username, moderation_enabled, last_active)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (group_id) DO UPDATE
            SET moderation_enabled = EXCLUDED.moderation_enabled, last_active = NOW(),
                title = COALESCE(EXCLUDED.title, groups.title),
                username = COALESCE(EXCLUDED.username, groups.username)
        """,
            group_id,
            title,
            username,
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
    """Get list of admins with positive credits.

    A PAYING seat: opened the bot, still an admin, has a balance. Shared with
    every other billing site — see `roster_predicates`.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT a.admin_id
            FROM administrators a
            JOIN group_administrators ga ON a.admin_id = ga.admin_id
            WHERE ga.group_id = $1 AND {PAYING_SEAT}
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
        # Find admin with highest balance among BILLABLE seats: `is_registered`
        # alone billed admins who had left the group (measured 2026-11-09: 2
        # live seats, one funding a group it left weeks earlier).
        admin_row = await conn.fetchrow(
            f"""
                SELECT a.admin_id, a.credits
                FROM administrators a
                JOIN group_administrators ga ON a.admin_id = ga.admin_id
                WHERE ga.group_id = $1 AND {BILLABLE_SEAT}
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


async def refund_credits(admin_id: int, amount: int) -> None:
    """Return credits to an admin when a charged action did not happen (#48).

    The mirror of ``deduct_credits_from_admins``: same table, same transaction
    record, opposite direction. Clearing ``credits_depleted_at`` matters — a
    refund restores a balance the depletion clock had already marked, and
    leaving the stamp in place would keep the admin on the deactivation path
    for a debt that was just repaid.

    Args:
        admin_id: The admin whose balance was charged.
        amount: Credits to return. Non-positive values are a no-op.
    """
    if amount <= 0:
        return

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
                UPDATE administrators
                SET credits = credits + $1, last_active = NOW(),
                    credits_depleted_at = NULL
                WHERE admin_id = $2
            """,
            amount,
            admin_id,
        )

        await conn.execute(
            """
                INSERT INTO transactions (admin_id, amount, type, description)
                VALUES ($1, $2, 'refund', 'Refund: charged action did not complete')
            """,
            admin_id,
            amount,
        )


async def cleanup_group_data(
    group_id: int,
    status: GroupStatus | str = GroupStatus.LEFT,
    reason: str | None = None,
) -> None:
    """Soft-remove a group (deletion-policy E+C).

    Mappings (group_administrators, approved_members) are hard-deleted — they are
    re-creatable on reactivation. The groups row is NEVER deleted: it transitions to
    `status` (paused/left) for audit + rollback, and an append-only event is logged.
    """
    logger.info(
        f"Deactivating database records for group {format_chat_log(group_id)} "
        f"(status={status.value if isinstance(status, GroupStatus) else status})"
    )

    status_value = status.value if isinstance(status, GroupStatus) else str(status)

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        # Snapshot the row before the transition (audit)
        old_row = await conn.fetchrow(
            "SELECT * FROM groups WHERE group_id = $1",
            group_id,
        )
        if old_row is None:
            logger.info(
                f"cleanup_group_data: group {format_chat_log(group_id)} "
                "not found — skipping cleanup"
            )
            return

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

        # Soft-disable the group itself (E+C: no hard delete)
        # NOTE: $1 must appear in text order (SET) and $2 after it (WHERE) so the
        # SQLite test adapter's positional $N→? rewrite binds correctly. Same query
        # works on asyncpg (Postgres $N is by number, not position).
        await conn.execute(
            """
            UPDATE groups
            SET status = $1, last_active = NOW()
            WHERE group_id = $2
            """,
            status_value,
            group_id,
        )

        # Append-only audit event
        await conn.execute(
            """
            INSERT INTO entity_events (entity_type, entity_id, action, reason, old_row)
            VALUES ('group', $1, $2, $3, $4)
            """,
            group_id,
            f"group_{status_value}",
            reason or "group_cleanup",
            json.dumps(dict(old_row), default=str) if old_row else None,
        )

    logger.info(
        f"Successfully deactivated database records for group {format_chat_log(group_id)} "
        f"(status={status_value})"
    )


async def _retire_group(
    conn,
    group_id: int,
    *,
    status: str,
    reason: str,
    migrated_to: int | None,
) -> bool:
    """The retire write, on a caller's connection. True if the row changed.

    Separate from `cleanup_group_data` on purpose (D1): retirement is an
    OBSERVATION (Telegram says the chat is gone, or the group was upgraded), and
    an observation must not destroy the paying-admin link. `cleanup_group_data`
    hard-deletes `group_administrators` and `approved_members`; this leaves both
    intact so a re-add and the billing report still resolve.

    Takes `conn` rather than acquiring its own so `migrate_group` can perform the
    copy and the retire inside ONE transaction.
    """
    old_row = await conn.fetchrow(
        "SELECT * FROM groups WHERE group_id = $1",
        group_id,
    )
    if old_row is None:
        return False

    # $N must appear in ascending text order: the SQLite test adapter rewrites
    # $N -> ? positionally (same constraint documented in cleanup_group_data).
    # `last_active` is deliberately NOT touched — it means last message activity,
    # and the `dormant` counter reads it (D5).
    result = await conn.execute(
        """
        UPDATE groups
        SET status = $1,
            bot_removed_at = COALESCE(bot_removed_at, NOW()),
            migrated_to_group_id = COALESCE($2, migrated_to_group_id)
        WHERE group_id = $3 AND status <> $4
        """,
        status,
        migrated_to,
        group_id,
        status,
    )
    if _affected_rows(result) == 0:
        # Already retired: idempotent, and no second event (D7).
        return False

    await conn.execute(
        """
        INSERT INTO entity_events (entity_type, entity_id, action, reason, old_row)
        VALUES ('group', $1, $2, $3, $4)
        """,
        group_id,
        "group_retired",
        reason,
        json.dumps(dict(old_row), default=str),
    )
    return True


async def retire_group(
    group_id: int,
    *,
    reason: str,
    status: GroupStatus | str = GroupStatus.LEFT,
    migrated_to: int | None = None,
) -> bool:
    """Retire a group row on observation — status + stamp + one event, nothing else.

    NOT `cleanup_group_data`: that hard-deletes `group_administrators` and
    `approved_members`, which is right for a deliberate leave and wrong for an
    observation — the paying-admin link must survive so a re-add and the billing
    report still resolve. Idempotent: a row already `left` changes nothing and
    emits no event (D7).
    """
    status_value = status.value if isinstance(status, GroupStatus) else str(status)
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        changed = await _retire_group(
            conn,
            group_id,
            status=status_value,
            reason=reason,
            migrated_to=migrated_to,
        )
    if changed:
        logger.info(f"Retired group {format_chat_log(group_id)} (reason={reason})")
    return changed


async def migrate_group(old_id: int, new_id: int) -> bool:
    """Move a group's identity and roster to the supergroup Telegram migrated it to.

    Identity and roster only (D3): the chat-keyed operational tables
    (`classification_verdicts`, `message_lookup_cache`, `recovery_attempts`,
    `spam_examples`, `message_classification_context`) are short-TTL junk, and
    `member_joins` is deliberately NOT moved — we have not observed joins in the
    new chat, so pre-existing trust must start closed there.
    """
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        old_row = await conn.fetchrow(
            "SELECT * FROM groups WHERE group_id = $1",
            old_id,
        )
        if old_row is None:
            logger.warning(
                f"migrate_group: {format_chat_log(old_id)} has no row — "
                f"nothing to migrate to {format_chat_log(new_id)}"
            )
            return False

        # Copy-not-clobber (D3): a row already at the destination is left exactly
        # as it is, so an operator's or an earlier job's lifecycle state there is
        # never overwritten. `ON CONFLICT ... DO NOTHING` only — NEVER DO UPDATE,
        # which the test adapter rewrites to INSERT OR REPLACE and which would
        # reset the destination row's columns.
        #
        # `bot_added_at` is carried VERBATIM (Q2): the observation was continuous
        # across the move, so the pre-existing window keeps the clock it had.
        # Neither NOW() (a fresh 72 h on unchanged evidence) nor NULL (which fails
        # is_pre_existing_candidate at its first leg, permanently).
        # `no_rights_detected_at` is omitted: rights at the destination are
        # unprobed, so the grace clock must start from an observation (D3).
        await conn.execute(
            """
            INSERT INTO groups (group_id, title, username, moderation_enabled,
                                status, linked_channel_id, created_at, last_active,
                                bot_added_at)
            SELECT $1, title, username, moderation_enabled, status,
                   linked_channel_id, NOW(), NOW(), bot_added_at
            FROM groups WHERE group_id = $2
            ON CONFLICT (group_id) DO NOTHING
            """,
            new_id,
            old_id,
        )

        # `is_registered` is carried, never defaulted: it is the billing link.
        await conn.execute(
            """
            INSERT INTO group_administrators
                (group_id, admin_id, is_registered, is_admin_now)
            SELECT $1, admin_id, is_registered, is_admin_now
            FROM group_administrators WHERE group_id = $2
            ON CONFLICT (group_id, admin_id) DO NOTHING
            """,
            new_id,
            old_id,
        )

        await conn.execute(
            """
            INSERT INTO approved_members
                (group_id, member_id, approved_at, moderation_event_count,
                 trust_source, trusted_until)
            SELECT $1, member_id, approved_at, moderation_event_count,
                   trust_source, trusted_until
            FROM approved_members WHERE group_id = $2
            ON CONFLICT (group_id, member_id) DO NOTHING
            """,
            new_id,
            old_id,
        )

        # Seat counts from COUNT rather than _affected_rows: that helper parses a
        # driver tag ("UPDATE 3"), and asyncpg tags an INSERT "INSERT 0 <n>" — so
        # parts[1] is the OID field and it reads 0. The counts below are log
        # context, not control flow, so a query is the honest source.
        seats = await conn.fetchval(
            "SELECT COUNT(*) FROM group_administrators WHERE group_id = $1", new_id
        )
        members = await conn.fetchval(
            "SELECT COUNT(*) FROM approved_members WHERE group_id = $1", new_id
        )

        changed = await _retire_group(
            conn,
            old_id,
            status=GroupStatus.LEFT.value,
            reason="migrated",
            migrated_to=new_id,
        )

    logger.info(
        f"Migrated group {format_chat_log(old_id)} -> {format_chat_log(new_id)}: "
        f"seats={seats} members={members} "
        f"(old row {'retired' if changed else 'already retired'})"
    )
    return changed


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
    """Return group IDs where no_rights_detected_at is set and past grace period.

    Excludes paused/left groups (E+C deletion policy): re-add is what clears the
    timestamp, and the no_rights job is only meant to act on currently-active rows.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT group_id
            FROM groups
            WHERE no_rights_detected_at IS NOT NULL
              AND no_rights_detected_at + make_interval(days => $1) <= NOW()
              AND status = $2
            """,
            grace_days,
            GroupStatus.ACTIVE.value,
        )
        return [row["group_id"] for row in rows]


async def get_admin_group_ids(admin_id: int) -> list[int]:
    """Get list of group IDs where admin is a member (DB only, no Telegram API)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT ga.group_id FROM group_administrators ga
            WHERE ga.admin_id = $1 AND {REGISTERED_SEAT}
            """,
            admin_id,
        )
        return [row["group_id"] for row in rows]


async def get_admin_groups(admin_id: int) -> list[dict]:
    """Get list of groups where user is an admin"""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT g.group_id, g.title, g.moderation_enabled,
                   g.topic_description_short, g.topic_updated_at,
                   g.status
            FROM groups g
            JOIN group_administrators ga ON g.group_id = ga.group_id
            WHERE ga.admin_id = $1 AND {REGISTERED_SEAT}
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
                        "topic_description_short": row["topic_description_short"],
                        "topic_updated_at": row["topic_updated_at"],
                        "status": row["status"],
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
                await cleanup_group_data(
                    group_id, status=GroupStatus.LEFT, reason="inaccessible_chat"
                )
            except Exception as e:  # noqa: BLE001
                logger.error(
                    f"Failed to cleanup inaccessible group {format_chat_log(group_id)}: {e}"
                )

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


async def get_trusted_member_row(group_id: int, member_id: int) -> dict | None:
    """The member's trust row when it grants trust, else None.

    The same predicate `is_trusted_member` applies, but it returns the row so
    a caller can also read `trust_source`. Issue #108 R5 needs that: an edit
    by a `pre_existing` sender is re-classified, so the caller has to know
    WHERE the trust came from, not merely that it exists.

    `trusted_until` bounds a reversible grant (issue #108: `pre_existing`
    trust expires at the window horizon). The comparison is done in Python,
    not in SQL, so it reads the same on PostgreSQL and on the SQLite test
    adapter. A grant whose expiry cannot be read is treated as EXPIRED —
    fail closed, never trust on a value we could not parse.
    """
    from .trust_operations import _as_datetime, get_member_trust_row

    row = await get_member_trust_row(group_id, member_id)
    if row is None:
        return None
    trusted_until = row.get("trusted_until")
    if trusted_until is not None:
        parsed = _as_datetime(trusted_until)
        if parsed is None or datetime.now(UTC) >= parsed:
            return None
    count = row.get("moderation_event_count")
    if count is None or count < get_probation_min_events():
        return None
    return row


async def is_trusted_member(group_id: int, member_id: int) -> bool:
    """True if member is approved, unexpired, and has completed probation."""
    return await get_trusted_member_row(group_id, member_id) is not None


async def increment_moderation_events(group_id: int, member_id: int) -> None:
    """Increment moderation event counter for an approved member.

    A moderated event is real evidence, so it supersedes an inferred grant:
    the row becomes `probation` and any `trusted_until` expiry is cleared.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE approved_members
            SET moderation_event_count = moderation_event_count + 1,
                trust_source = 'probation',
                trusted_until = NULL
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
            INSERT INTO approved_members (
                group_id, member_id, moderation_event_count, trust_source
            )
            VALUES ($1, $2, $3, 'admin')
            ON CONFLICT (group_id, member_id) DO UPDATE
            SET moderation_event_count = EXCLUDED.moderation_event_count,
                trust_source = 'admin',
                trusted_until = NULL
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
            INSERT INTO approved_members (
                group_id, member_id, moderation_event_count, trust_source
            )
            VALUES ($1, $2, 1, 'probation')
            ON CONFLICT DO NOTHING
            RETURNING member_id
        """,
            group_id,
            member_id,
        )
        return row is not None


async def remove_member_from_group(member_id: int, group_id: int | None = None) -> None:
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

        # E+C: re-add reactivates a paused/left group + clears no_rights_detected_at
        # (avoids the daily no-rights job re-triggering leave on a stale timestamp).
        # Fresh inserts and still-active groups have status='active' — IF branch skipped.
        prior = await conn.fetchrow(
            "SELECT status FROM groups WHERE group_id = $1", group_id
        )
        if prior and prior["status"] != GroupStatus.ACTIVE.value:
            # D6b: the reactivation must ALSO clear `bot_removed_at`, which is not
            # a duplicate of `record_bot_added`'s clear — that runs on the add
            # path, this branch runs when the row already existed and was retired
            # by the sweep (or left). Leaving the stamp behind would read as
            # "removed, then still removed" to anything that inspects it, and
            # `is_pre_existing_candidate` would see a removal it must distrust.
            # Issue #41: reactivation must ALSO restore moderation_enabled.
            # A row with status != active can only have come from
            # cleanup_group_data, i.e. the bot left the group — the low-balance
            # leave disables moderation first (handle_deactivation), the
            # no-rights leave leaves the flag alone. Either way the customer is
            # coming back and wants moderation. Without this, the row
            # reactivated to status=active with moderation_enabled=false and
            # `validation` dropped every message: bot present, rights intact,
            # credits topped up, and silently unmoderated for ~36 days.
            # Awaiting-rights rows are NOT touched here — they carry
            # status='active' by schema default, so this branch cannot fire for
            # them; activate_discussion_group handles those on promotion.
            await conn.execute(
                """
                UPDATE groups
                SET status = $1, no_rights_detected_at = NULL,
                    bot_removed_at = NULL,
                    moderation_enabled = TRUE
                WHERE group_id = $2
                """,
                GroupStatus.ACTIVE.value,
                group_id,
            )
            logger.info(
                f"Reactivated group {format_chat_log(group_id)} on re-add "
                "and restored moderation_enabled"
            )
            await conn.execute(
                """
                INSERT INTO entity_events (entity_type, entity_id, action, reason)
                VALUES ('group', $1, $2, $3)
                """,
                group_id,
                "group_reactivated",
                "re_add",
            )

        # Handle both old format (just IDs) and new format (IDs with usernames)
        usernames = cast(
            "list[str | None]",
            admin_usernames if admin_usernames is not None else [None] * len(admin_ids),
        )

        # Ensure we have usernames for all admins
        if len(usernames) != len(admin_ids):
            raise ValueError("admin_ids and admin_usernames must have the same length")

        # Add/update admins
        for admin_id, username in zip(admin_ids, usernames):
            # Skip GROUP_ANONYMOUS_BOT_ID — it appears in admin lists for groups with
            # anonymous admin enabled but cannot receive bot-to-bot DMs.
            if admin_id == GROUP_ANONYMOUS_BOT_ID:
                continue

            # Save or update admin with username if provided
            admin = await admin_operations.get_admin(admin_id)
            if admin is None:
                # Bot-add is a BILLING writer: the admin who adds the bot has
                # a private chat with it by construction. So this grants
                # INITIAL_CREDITS *and records the grant* — a real `initial`
                # transaction plus a non-NULL `activated_at` — on this same
                # connection, in this same transaction as the seat.
                #
                # Recording it is what stops the double grant.
                # `initialize_new_admin` keys on `activated_at`: left NULL, the
                # admin's first `/start` would claim the row and add a SECOND
                # INITIAL_CREDITS — 200 credits against one 100-credit grant.
                # The boot backfill used to hide that by stamping the row
                # before the user could open the bot; it is a migration step
                # now and no longer runs on boot, so the record has to be real.
                #
                # `DO NOTHING`, never `DO UPDATE`: the test harness rewrites an
                # upsert into INSERT OR REPLACE, which deletes and re-inserts
                # the row and resets every column the INSERT list does not
                # name — and a blind overwrite would zero a balance someone
                # topped up. RETURNING rather than `_affected_rows`, which
                # parses "UPDATE n" and reads an INSERT's OID field instead.
                inserted = await conn.fetchrow(
                    """
                    INSERT INTO administrators (
                        admin_id, username, credits, moderation_mode,
                        is_active, activated_at, created_at, last_active
                    ) VALUES ($1, $2, $3, 'notify', TRUE, NOW(), NOW(), NOW())
                    ON CONFLICT DO NOTHING
                    RETURNING admin_id
                    """,
                    admin_id,
                    username,
                    admin_operations.INITIAL_CREDITS,
                )
                if inserted is not None:
                    await conn.execute(
                        """
                        INSERT INTO transactions (admin_id, amount, type, description)
                        VALUES ($1, $2, 'initial', 'Initial credits (bot added to group)')
                        """,
                        admin_id,
                        admin_operations.INITIAL_CREDITS,
                    )
                else:
                    # Raced with another writer between the read and the
                    # insert: the existing row keeps its balance and its
                    # activation, and only a missing username is filled in.
                    await conn.execute(
                        """
                        UPDATE administrators SET username = COALESCE($1, username)
                        WHERE admin_id = $2
                        """,
                        username,
                        admin_id,
                    )
            else:
                # A KNOWN admin. Only a missing username is worth writing, and
                # it is written as ONE column — never `save_admin`, which is a
                # whole-row INSERT ... ON CONFLICT DO UPDATE. Two reasons:
                # `save_admin` rewrites `credits` and `moderation_mode` from a
                # snapshot read a moment ago (a re-add must not undo a top-up
                # that landed in between), and the test harness rewrites that
                # upsert into INSERT OR REPLACE, which deletes the row and
                # re-inserts it — silently clearing `activated_at`, the very
                # column that proves the grant. Postgres would keep it; the
                # harness would not, and a test that cannot see the column
                # cannot prove the invariant.
                if admin.username is None and username is not None:
                    await conn.execute(
                        "UPDATE administrators SET username = $1 WHERE admin_id = $2",
                        username,
                        admin_id,
                    )

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


async def update_group_metadata(
    group_id: int,
    title: str | None = None,
    username: str | None = None,
    linked_channel_id: int | None = None,
) -> None:
    """Fill NULL metadata fields on a groups row (never clobbers existing values).

    Used by the chat-topic scan to heal stale rows (issue #5): when a
    discussion group's linked channel is discovered live, the resolved channel
    id and any missing title/username are persisted so `/stats` metadata is
    complete and future scans skip the get_chat round-trip. COALESCE semantics:
    only NULL fields are filled; existing values are never overwritten.
    Does not touch moderation_enabled or the topic_* columns.

    Args:
        group_id: The groups row to update
        title: Title to fill if currently NULL (e.g. from Telegram get_chat)
        username: Username to fill if currently NULL
        linked_channel_id: Linked channel id to fill if currently NULL
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE groups
            SET title = COALESCE($1, title),
                username = COALESCE($2, username),
                linked_channel_id = COALESCE($3, linked_channel_id)
            WHERE group_id = $4
            """,
            title,
            username,
            linked_channel_id,
            group_id,
        )


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


async def heal_bare_group_rows(
    concurrency: int = 5, limit: int = 500
) -> dict[str, int]:
    """Resolve bare groups rows (title/username NULL) via the Telegram API.

    Closes the no-metadata gap going forward: any row created without chat
    metadata (e.g. a bare set_group_moderation insert) is healed by the daily
    scheduled job within 24h. Fills title, username and linked_channel_id
    (COALESCE-only — never overwrites existing values). Chats the bot cannot
    reach (forbidden / bad request / chat not found) or that resolve to
    non-group chats (no title) are skipped and counted, never raised — the
    loop must survive a single dead row.

    Args:
        concurrency: max parallel bot.get_chat calls (Telegram rate-limit safe).
        limit: max rows to attempt per run.

    Returns:
        Summary dict: {"healed": n, "skipped": n, "total": n}.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT group_id FROM groups
            WHERE title IS NULL AND username IS NULL
            ORDER BY group_id
            LIMIT $1
            """,
            limit,
        )
    if not rows:
        logger.info("heal_bare_group_rows: no bare rows to heal")
        return {"healed": 0, "skipped": 0, "total": 0}

    healed = 0
    skipped = 0
    sem = asyncio.Semaphore(concurrency)

    async def heal_one(group_id: int) -> None:
        nonlocal healed, skipped
        async with sem:
            try:
                chat = await bot.get_chat(group_id)
                chat_title = getattr(chat, "title", None)
                if not chat_title:
                    # Private chats / irregular rows: never healable, leave for
                    # the purge flow instead of re-selecting them every day.
                    logger.info(
                        f"heal_bare_group_rows: {format_chat_log(group_id)} "
                        "resolves to a chat without a title — skipping"
                    )
                    skipped += 1
                    return
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        UPDATE groups
                        SET title = COALESCE($1, title),
                            username = COALESCE($2, username),
                            linked_channel_id = COALESCE($3, linked_channel_id)
                        WHERE group_id = $4
                        """,
                        chat_title,
                        getattr(chat, "username", None),
                        getattr(chat, "linked_chat_id", None),
                        group_id,
                    )
                healed += 1
            except (
                TelegramBadRequest,
                TelegramForbiddenError,
                TelegramRetryAfter,
                TelegramNetworkError,
                TelegramAPIError,
            ) as e:
                # Telegram-side failures (chat inaccessible): expected, skip quietly
                logger.info(
                    f"heal_bare_group_rows: skipping inaccessible chat "
                    f"{format_chat_log(group_id)}: {e}"
                )
                skipped += 1
            except Exception as e:  # noqa: BLE001
                # DB errors / unexpected failures: must NOT be swallowed as skips —
                # a persistent DB issue would silently retry every bare row forever
                # with no escalation (PR review finding).
                logger.error(
                    f"heal_bare_group_rows: DB/unknown failure for "
                    f"{format_chat_log(group_id)}: {e}"
                )

    await asyncio.gather(*(heal_one(row["group_id"]) for row in rows))
    logger.info(
        f"heal_bare_group_rows: healed={healed} skipped={skipped} total={len(rows)}"
    )
    return {"healed": healed, "skipped": skipped, "total": len(rows)}


def _affected_rows(result: str | None) -> int:
    """Row count from a driver's status string ("UPDATE 3"), 0 when absent."""
    if not result:
        return 0
    parts = result.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return 0
    return int(parts[1])


async def upsert_group_admin(
    conn, group_id: int, admin_id: int, is_admin_now: bool = True
) -> None:
    """Add a Telegram admin to the roster, or flip their current-admin flag.

    Two statements on purpose — never `ON CONFLICT ... DO UPDATE`. The test
    harness rewrites a `DO UPDATE` upsert into `INSERT OR REPLACE`, and REPLACE
    deletes the row and re-inserts it, resetting every column the INSERT list
    does not name. With `is_registered` unnamed, that silently turned a
    roster-only row into a billing row (FALSE → TRUE) in tests only, while
    Postgres did the right thing. `DO NOTHING` + a one-column UPDATE is immune
    to the rewrite: IGNORE never fires on an existing row, and the UPDATE
    touches exactly one column.

    The insert seeds `is_registered` from the holder's OWN record rather than
    from a constant. A Telegram admin we discovered is roster-only — skipped
    from moderation, never billed, never DM'd — *unless* they have already
    proved they are reachable: an `activated_at`, a balance, or the `initial`
    transaction that is the grant record. Those three are the whole billing
    evidence in this schema, and the balance and grant terms are there because
    the legacy bot-add rows carry one or the other with no activation at all.

    A bare 0-credit / NULL-`activated_at` holder still seeds `FALSE`, so a
    stranger discovered in an admin list is not silently turned into a billing
    contact. The value is read in Python rather than as a subquery inside
    `VALUES`: that construct is the one most likely to pass Postgres and fail
    the SQLite harness.

    The `administrators` row is seeded FIRST, here rather than at each call
    site, because `group_administrators.admin_id` carries a foreign key to it:
    a seat for an unknown user is refused by Postgres, and the SQLite harness
    does not enforce foreign keys, so the wrong order passes every test and
    fails on the first real promotion.
    """
    await seed_administrator_row(conn, admin_id)
    registered = await conn.fetchval(
        """
        SELECT (
            activated_at IS NOT NULL
            OR credits > 0
            OR EXISTS (
                SELECT 1 FROM transactions t
                WHERE t.admin_id = administrators.admin_id AND t.type = 'initial'
            )
        ) AS registered
        FROM administrators WHERE admin_id = $1
        """,
        admin_id,
    )
    await conn.execute(
        """
        INSERT INTO group_administrators
            (group_id, admin_id, is_registered, is_admin_now)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT DO NOTHING
        """,
        group_id,
        admin_id,
        bool(registered),
        is_admin_now,
    )
    await conn.execute(
        """
        UPDATE group_administrators SET is_admin_now = $1
        WHERE group_id = $2 AND admin_id = $3
        """,
        is_admin_now,
        group_id,
        admin_id,
    )


async def seed_administrator_row(conn, admin_id: int) -> None:
    """Give a Telegram admin an `administrators` row with 0 credits, if absent.

    Roster prefill must never go through `admin_operations.save_admin` or
    `update_group_admins`'s creation branch: both hand out `INITIAL_CREDITS`,
    which would pay 100 credits to every stranger we enumerate. 0 credits is
    the honest state — they have not registered, and `initialize_new_admin`
    grants on `activated_at`, not on row existence.

    An existing row is untouched, so a paying admin keeps their balance.
    """
    await conn.execute(
        """
        INSERT INTO administrators (admin_id, credits) VALUES ($1, 0)
        ON CONFLICT DO NOTHING
        """,
        admin_id,
    )


async def mark_admins_not_admin(conn, group_id: int, keep_ids: list[int]) -> int:
    """Flip `is_admin_now = FALSE` for roster rows Telegram no longer lists.

    Demotion is a FLAG, never a delete: the row may still carry billing rights
    (`is_registered`), and deleting it would silently stop charging the payer.

    An EMPTY `keep_ids` is refused, not executed. The predicate is
    `admin_id NOT IN (...)`, which SQLite accepts with an empty list and
    evaluates as true for every row — one failed or empty `getChatAdministrators`
    read would demote the whole group. Returns the number of rows flipped.
    """
    if not keep_ids:
        logger.warning(
            f"mark_admins_not_admin: refusing an empty admin list for "
            f"{format_chat_log(group_id)} — nothing demoted"
        )
        return 0
    placeholders = ", ".join(f"${i}" for i in range(2, len(keep_ids) + 2))
    result = await conn.execute(
        f"""
        UPDATE group_administrators SET is_admin_now = FALSE
        WHERE group_id = $1 AND admin_id NOT IN ({placeholders})
        """,
        group_id,
        *keep_ids,
    )
    return _affected_rows(result)


class SyncReport(NamedTuple):
    """What one roster sync saw, and (when applied) what it wrote.

    The counts are computed from the read, not from the driver's affected-row
    totals, so a dry run and the `--apply` that follows it report the SAME
    numbers. That is the point of a dry run: a preview that counts differently
    from the write is a preview of a different operation.
    """

    chat_id: int
    title: str | None
    telegram: int  # human admins Telegram listed, after filtering
    known: int  # seats already on the roster as current admins
    new: int  # seats the roster had never seen
    demoted: int  # roster rows Telegram no longer lists as admins
    applied: bool

    def as_line(self) -> str:
        return (
            f"telegram={self.telegram} known={self.known} new={self.new} "
            f"demoted={self.demoted}"
        )

async def sync_group_administrators(chat_id: int, *, apply: bool = False) -> SyncReport:
    """Reconcile one group's admin roster against Telegram's own list.

    Telegram is the authority on who is an admin right now; the roster is our
    memory of it, and until #130 nothing refreshed that memory except the
    moment the bot was added. The measured result: 84 admin seats across 72
    users against 39 roster rows, 45 seats invisible in 13 groups — so
    `validation` skipped 39 of the 72 humans it was supposed to skip, and
    charged paying admins for their own messages.

    Filtering is the same predicate the notify paths already use: bots cannot
    be moderated or billed, and GROUP_ANONYMOUS_BOT_ID is the sender id of
    "anonymous admin" posts — not a person, and not a user who can receive a
    DM.

    `apply=False` is a pure read. Writes go through the #130 primitives only —
    `upsert_group_admin` seeds the `administrators` row itself, which is what
    keeps the foreign key satisfied in one place instead of at each call site.

    Nothing here touches `is_registered`: discovering a stranger as a Telegram
    admin must never make them a billing contact.
    """
    admins = await bot.get_chat_administrators(chat_id)

    seen: list[int] = []
    for admin in admins:
        user = admin.user
        if user.is_bot or user.id == GROUP_ANONYMOUS_BOT_ID:
            continue
        if user.id not in seen:
            seen.append(user.id)

    pool = await get_pool()
    async with pool.acquire() as conn:
        group_row = await conn.fetchrow(
            "SELECT title FROM groups WHERE group_id = $1", chat_id
        )
        rows = await conn.fetch(
            """
            SELECT admin_id, is_admin_now FROM group_administrators
            WHERE group_id = $1
            """,
            chat_id,
        )

        current = {row["admin_id"]: _flag(row["is_admin_now"]) for row in rows}
        seen_set = set(seen)
        new_ids = [admin_id for admin_id in seen if admin_id not in current]
        known = sum(1 for admin_id in seen if current.get(admin_id))
        # Predicted from the read, so a dry run counts exactly what --apply
        # will write.
        if seen:
            demoted = sum(
                1
                for admin_id, is_admin in current.items()
                if is_admin and admin_id not in seen_set
            )
        else:
            # An empty (or bot-only) admin list is a failed read, not a group
            # with no admins. Demoting the whole roster on it would blank the
            # skip set for a live group — so NEITHER the write pass NOR the
            # preview may report a demotion here. A dry run that promises a
            # write `--apply` refuses is a preview that lies about the write,
            # and that preview is the only thing standing between an operator
            # and an irreversible roster change.
            logger.warning(
                f"sync_group_administrators: Telegram listed no human admins "
                f"for {format_chat_log(chat_id)} — nothing to write"
            )
            demoted = 0

        if apply and seen:
            async with conn.transaction():
                for admin_id in seen:
                    await upsert_group_admin(conn, chat_id, admin_id)
                if demoted:
                    await mark_admins_not_admin(conn, chat_id, seen)

    return SyncReport(
        chat_id=chat_id,
        title=group_row["title"] if group_row else None,
        telegram=len(seen),
        known=known,
        new=len(new_ids),
        demoted=demoted,
        applied=apply,
    )

async def set_admin_now(
    conn, group_id: int, admin_id: int, is_admin_now: bool
) -> int:
    """Flip ONE seat's `is_admin_now`, never creating a row.

    The `chat_member` demotion path needs the update half of
    `upsert_group_admin` without its insert half: a demotion of someone we
    never listed must not ADD a roster row for them — that would grow the
    roster with every mute in a group the bot watches. Returns rows affected,
    so a caller can tell "we knew about this seat" from "we did not".
    """
    result = await conn.execute(
        """
        UPDATE group_administrators SET is_admin_now = $1
        WHERE group_id = $2 AND admin_id = $3
        """,
        is_admin_now,
        group_id,
        admin_id,
    )
    return _affected_rows(result)

# A group is DORMANT — quiet rather than dead — when nothing has happened in it
# for this long AND no paying admin is attached. Dormancy is a report, never an
# action (D4): a quiet group with a payer still gets its roster refreshed, and a
# quiet group without one is named in the summary so an operator can decide.
DORMANT_AFTER_DAYS = 90

async def sweep_group_administrators(
    concurrency: int = 5, limit: int = 500
) -> dict[str, int]:
    """Re-sync every active group's roster from Telegram (the daily writer).

    The two event-driven writers (bot-add, `chat_member`) only fire while the
    bot is present and only for the change that just happened; a promotion made
    before the bot joined, or an update the bot was down for, is invisible to
    both. This is the leg that closes those gaps, and it is why the roster is
    not merely best-effort.

    Shaped on `heal_bare_group_rows`: a per-group failure is COUNTED and the
    sweep continues, because one group the bot was kicked from must not cost
    the other 46 their refresh.

    Args:
        concurrency: max parallel Telegram reads.
        limit: max groups per run.

    A per-group failure is CLASSIFIED rather than merely counted (#130): a chat
    Telegram says is gone is retired, a chat that moved is migrated, and a bot
    that lost only its member-list rights is routed to the existing no-rights
    grace flow instead of being retired — that last one is a paying group whose
    roster we cannot read, and retiring it would stop its moderation (D2). An
    error no predicate claims stays a loud WARNING and counts as `failed`.

    Returns:
        {"synced": n, "failed": n, "retired": n, "migrated": n, "no_rights": n,
         "dormant": n, "new": n, "demoted": n, "total": n}.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Both dormant inputs are computed in SQL: `last_active` is a timestamp
        # and parsing it in Python would duplicate what the database already
        # knows. `has_paying_admin` is the same predicate
        # `get_paid_groups_with_moderation_off` uses — a group whose admins are
        # all at zero credits is unmoderated by design, not by defect.
        rows = await conn.fetch(
            f"""
            SELECT g.group_id,
                   (g.last_active < NOW() - INTERVAL '{DORMANT_AFTER_DAYS} days')
                       AS is_stale,
                   EXISTS (
                       SELECT 1
                       FROM group_administrators ga
                       JOIN administrators a ON a.admin_id = ga.admin_id
                       WHERE ga.group_id = g.group_id
                         AND {PAYING_SEAT}
                   ) AS has_paying_admin
            FROM groups g
            WHERE g.status = 'active'
            ORDER BY g.group_id
            LIMIT $1
            """,
            limit,
        )
    if not rows:
        logger.info("sweep_group_administrators: no active groups to sync")
        return {
            "synced": 0,
            "failed": 0,
            "retired": 0,
            "migrated": 0,
            "no_rights": 0,
            "dormant": 0,
            "new": 0,
            "demoted": 0,
            "total": 0,
        }

    counts = {
        "synced": 0,
        "failed": 0,
        "retired": 0,
        "migrated": 0,
        "no_rights": 0,
        "dormant": 0,
        "new": 0,
        "demoted": 0,
    }
    sem = asyncio.Semaphore(concurrency)

    async def act(group_id: int, exc: Exception) -> str:
        """Classify one per-group failure and take its action.

        Returns the summary bucket: `"migrated"`, `"no_rights"`, `"retired"`,
        `"failed"` (classified, but its own write raised — already logged here),
        or `"unknown"` (no predicate claimed the exception, so `sync_one` logs
        today's warning and counts it as `failed`).

        Never raises: an action's own database error must not abort the gather
        and cost the remaining groups their refresh.

        Order is load-bearing — `migrate_target_id` is consulted FIRST.
        `TelegramMigrateToChat` is neither a `TelegramBadRequest` nor matched by
        the inaccessible markers, so today no ordering flips a test; a future
        aiogram that reparents it must not silently demote a migration into a
        retirement.
        """
        target = migrate_target_id(exc)
        if target is not None:
            try:
                await migrate_group(group_id, target)
            except Exception as act_exc:
                logger.warning(
                    f"sweep: migrate of {format_chat_log(group_id)} to "
                    f"{format_chat_log(target)} failed — "
                    f"{type(act_exc).__name__}: {act_exc}",
                    exc_info=True,
                )
                return "failed"
            logger.info(
                f"sweep: migrated {format_chat_log(group_id)} to "
                f"{format_chat_log(target)}"
            )
            return "migrated"
        if is_member_list_inaccessible_error(exc):
            # D2: rights, not absence. This is a group we cannot READ, and it may
            # be a paying one — retiring it would silently stop its moderation.
            # Route it into the existing no-rights grace flow instead.
            try:
                await set_no_rights_detected_at(group_id)
            except Exception as act_exc:
                logger.warning(
                    f"sweep: no-rights flag of {format_chat_log(group_id)} "
                    f"failed — {type(act_exc).__name__}: {act_exc}",
                    exc_info=True,
                )
                return "failed"
            logger.info(
                f"sweep: {format_chat_log(group_id)} lacks member-list rights — "
                "routed to the no-rights grace flow"
            )
            return "no_rights"
        if is_group_inaccessible_error(exc):
            try:
                await retire_group(group_id, reason="sweep_inaccessible")
            except Exception as act_exc:
                logger.warning(
                    f"sweep: retirement of {format_chat_log(group_id)} "
                    f"failed — {type(act_exc).__name__}: {act_exc}",
                    exc_info=True,
                )
                return "failed"
            logger.info(
                f"sweep: retired {format_chat_log(group_id)} as inaccessible"
            )
            return "retired"
        return "unknown"

    async def sync_one(row) -> None:
        group_id = row["group_id"]
        async with sem:
            try:
                report = await sync_group_administrators(group_id, apply=True)
            except Exception as exc:  # noqa: BLE001 — per-group isolation
                bucket = await act(group_id, exc)
                if bucket == "unknown":
                    # The one unclassified channel stays LOUD: an error we cannot
                    # name is a defect, not a lifecycle event.
                    logger.warning(
                        f"sweep_group_administrators: {format_chat_log(group_id)} "
                        f"skipped — {type(exc).__name__}: {exc}"
                    )
                    bucket = "failed"
                counts[bucket] += 1
                return
            counts["synced"] += 1
            counts["new"] += report.new
            counts["demoted"] += report.demoted
            # Dormant is counted only for groups we actually synced: a retired or
            # migrated row is not a live quiet group, and counting it would
            # double-report the same groups the retirement buckets already name.
            if row["is_stale"] and not row["has_paying_admin"]:
                counts["dormant"] += 1

    await asyncio.gather(*(sync_one(row) for row in rows))
    summary = {**counts, "total": len(rows)}
    logger.info(f"sweep_group_administrators: {summary}")
    return summary

async def get_paid_groups_with_moderation_off() -> list[dict]:
    """Active groups with moderation off while a PAYING admin is attached (issue #41).

    This is the silent-death state: `validation` drops every message when
    `moderation_enabled` is false, so the group reads healthy on every outward
    signal — the bot is in the chat, its rights are intact, the balance is
    topped up — while catching nothing and never deducting a credit. It went
    unnoticed for ~36 days on a paying customer's groups and was reported by the
    customer, not by any surface we had.

    Only PAYING admins count: a group whose admins are all at zero credits is
    unmoderated by design (the low-balance path paused it), not by defect.

    Returns one row per offending group, empty when healthy.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT g.group_id,
                   g.title,
                   count(ga.admin_id) AS paying_admin_count,
                   max(a.credits) AS max_credits
            FROM groups g
            JOIN group_administrators ga ON ga.group_id = g.group_id
            JOIN administrators a ON a.admin_id = ga.admin_id
            WHERE g.status = $1
              AND g.moderation_enabled = false
              AND {PAYING_SEAT}
            GROUP BY g.group_id, g.title
            ORDER BY g.group_id
            """,
            GroupStatus.ACTIVE.value,
        )
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------------
# #130 one-shot repair — reset the boot-stamped strangers, promote the proven
# ---------------------------------------------------------------------------

def _in_placeholders(values: list[int], start: int = 1) -> str:
    """`$1, $2, …` for an IN clause; pass the values in the same order."""
    return ", ".join(f"${i}" for i in range(start, start + len(values)))


def roster_repair_guard_failures(report: dict) -> list[str]:
    """The independent guards that must read 0 before the repair writes.

    Both are checks on the RESET cohort, and either one means the predicate is
    reaching rows it has no business touching: a row carrying any transaction
    has a billing history, and a row holding a registered seat is somebody's
    current payer. A refusal, never a warning.
    """
    failures = []
    if report["reset_with_any_transaction"]:
        failures.append(
            f"{report['reset_with_any_transaction']} reset candidate(s) carry a "
            "transaction — the predicate is reaching billing history"
        )
    if report["reset_with_registered_seat"]:
        failures.append(
            f"{report['reset_with_registered_seat']} reset candidate(s) hold a "
            "registered seat — they are someone's payer"
        )
    return failures


#: A row this predicate matches was never activated by its holder: no grant
#: record (`initial` transaction) and no balance. Both guards are POSITIVE
#: evidence, and both are load-bearing — the balance guard is what protects
#: the legacy rows the old bot-add branch paid without writing a transaction.
ROSTER_RESET_PREDICATE = """
    a.activated_at IS NOT NULL
      AND a.credits = 0
      AND NOT EXISTS (
          SELECT 1 FROM transactions t
          WHERE t.admin_id = a.admin_id AND t.type = 'initial'
      )
"""


async def collect_roster_repair(
    conn, group_ids: list[int] | None = None
) -> dict:
    """Read the repair's two cohorts. Writes nothing.

    The ONE read the dry run and `--apply` both use, so the preview and the
    write agree by construction rather than by a second counting path.

    The promote cohort EXCLUDES the reset cohort on purpose. In the live state
    those rows still carry `activated_at`, so a promote read taken before the
    reset counts them too (62 seats instead of 6) and promises a write the
    apply would refuse. Subtracting them here is what makes the dry run's
    number the apply's number.
    """
    scope_params: list[int] = []
    reset_scope = promote_scope = ""
    if group_ids:
        placeholders = _in_placeholders(group_ids)
        reset_scope = (
            " AND a.admin_id IN (SELECT admin_id FROM group_administrators "
            f"WHERE group_id IN ({placeholders}))"
        )
        promote_scope = f" AND ga.group_id IN ({placeholders})"
        scope_params = list(group_ids)

    reset_rows = await conn.fetch(
        f"""
        SELECT a.admin_id, a.created_at
        FROM administrators a
        WHERE {ROSTER_RESET_PREDICATE} {reset_scope}
        ORDER BY a.admin_id
        """,
        *scope_params,
    )
    reset_ids = [row["admin_id"] for row in reset_rows]

    cohort: dict[str, int] = {}
    for row in reset_rows:
        key = str(row["created_at"])
        cohort[key] = cohort.get(key, 0) + 1

    reset_seats = reset_groups = 0
    reset_with_any_transaction = reset_with_registered_seat = 0
    if reset_ids:
        placeholders = _in_placeholders(reset_ids)
        reset_seats = await conn.fetchval(
            "SELECT COUNT(*) FROM group_administrators "
            f"WHERE admin_id IN ({placeholders})",
            *reset_ids,
        )
        reset_groups = await conn.fetchval(
            "SELECT COUNT(DISTINCT group_id) FROM group_administrators "
            f"WHERE admin_id IN ({placeholders})",
            *reset_ids,
        )
        # Independent second guard: a reset candidate carrying ANY transaction
        # has a billing history and must not be touched.
        reset_with_any_transaction = await conn.fetchval(
            "SELECT COUNT(DISTINCT admin_id) FROM transactions "
            f"WHERE admin_id IN ({placeholders})",
            *reset_ids,
        )
        # Third guard: a candidate holding a REGISTERED seat is someone's
        # current payer. Reset would drop them out of billing silently.
        reset_with_registered_seat = await conn.fetchval(
            "SELECT COUNT(DISTINCT admin_id) FROM group_administrators "
            f"WHERE is_registered AND admin_id IN ({placeholders})",
            *reset_ids,
        )

    exclude_reset = ""
    promote_params = list(scope_params)
    if reset_ids:
        exclude_reset = (
            " AND ga.admin_id NOT IN "
            f"({_in_placeholders(reset_ids, len(promote_params) + 1)})"
        )
        promote_params.extend(reset_ids)

    promote_rows = await conn.fetch(
        f"""
        SELECT ga.group_id, ga.admin_id, a.username, g.title
        FROM group_administrators ga
        JOIN administrators a ON a.admin_id = ga.admin_id
        LEFT JOIN groups g ON g.group_id = ga.group_id
        WHERE NOT ga.is_registered
          AND ga.is_admin_now
          AND a.activated_at IS NOT NULL
          {promote_scope} {exclude_reset}
        ORDER BY ga.group_id, ga.admin_id
        """,
        *promote_params,
    )

    # What a promote run WITHOUT the reset first would take. Printed beside the
    # real number so the ordering is visible in every run, not just in a memo.
    promote_without_reset = await conn.fetchval(
        f"""
        SELECT COUNT(*)
        FROM group_administrators ga
        JOIN administrators a ON a.admin_id = ga.admin_id
        WHERE NOT ga.is_registered
          AND ga.is_admin_now
          AND a.activated_at IS NOT NULL
          {promote_scope}
        """,
        *scope_params,
    )

    return {
        "reset_admin_ids": reset_ids,
        "reset_admins": len(reset_ids),
        "reset_seats": reset_seats,
        "reset_groups": reset_groups,
        "reset_created_at": cohort,
        "reset_with_any_transaction": reset_with_any_transaction,
        "reset_with_registered_seat": reset_with_registered_seat,
        "promote_rows": [dict(row) for row in promote_rows],
        "promote_seats": len(promote_rows),
        "promote_admins": len({row["admin_id"] for row in promote_rows}),
        "promote_groups": len({row["group_id"] for row in promote_rows}),
        "promote_without_reset": promote_without_reset,
        "applied": False,
        "promote_written": 0,
        "after": None,
    }


async def repair_roster_activation(
    conn, *, apply: bool = False, group_ids: list[int] | None = None
) -> dict:
    """Run the #130 repair. Read-only unless `apply` is true.

    Two phases in ONE transaction, **reset first**. The order is load-bearing:
    promoting before the reset would make the boot-stamped strangers billing
    contacts, because at that moment they still carry `activated_at`.

    After the writes the same read runs again inside the transaction, so the
    report says what the state became rather than what the statements were
    expected to do. A second `--apply` therefore reads `0 / 0` by itself.

    A tripped guard REFUSES: the report carries `refused` and nothing is
    written. Both guards are on the reset cohort, and either one means the
    predicate is reaching rows it has no business touching.
    """
    if not apply:
        report = await collect_roster_repair(conn, group_ids)
        failures = roster_repair_guard_failures(report)
        if failures:
            report["refused"] = failures
        return report

    async with conn.transaction():
        report = await collect_roster_repair(conn, group_ids)

        failures = roster_repair_guard_failures(report)
        if failures:
            report["refused"] = failures
            return report

        reset_ids = report["reset_admin_ids"]
        if reset_ids:
            await conn.execute(
                "UPDATE administrators SET activated_at = NULL "
                f"WHERE admin_id IN ({_in_placeholders(reset_ids)})",
                *reset_ids,
            )

        # The design's statement, verbatim: after the reset the boot-stamped
        # rows read NULL, so `activated_at IS NOT NULL` is the whole filter.
        promote_scope = ""
        promote_params: list[int] = []
        if group_ids:
            promote_scope = (
                f" AND group_id IN ({_in_placeholders(group_ids)})"
            )
            promote_params = list(group_ids)
        result = await conn.execute(
            f"""
            UPDATE group_administrators SET is_registered = TRUE
            WHERE NOT is_registered
              AND is_admin_now
              AND admin_id IN (
                  SELECT admin_id FROM administrators
                  WHERE activated_at IS NOT NULL
              )
              {promote_scope}
            """,
            *promote_params,
        )
        report["promote_written"] = _affected_rows(result)

        after = await collect_roster_repair(conn, group_ids)
        report["applied"] = True
        report["after"] = {
            "reset_admins": after["reset_admins"],
            "reset_seats": after["reset_seats"],
            "promote_seats": after["promote_seats"],
        }
        return report
