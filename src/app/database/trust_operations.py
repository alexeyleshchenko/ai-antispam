"""Trust provenance for members who were already in a group when the bot arrived.

A group that adds the bot *after* it already has members has those members
classified cold: every message they post costs one LLM call and lands a
"new sender" review on the admins — exactly when the bot is newest and the
group's own history is the only thing that vetted them.

"Joined before the bot was added" is NOT observable. Telegram exposes no join
date, and a bot cannot enumerate a chat's members, so the property can only be
inferred NEGATIVELY: *this sender has no join event on record, therefore it was
already here when the bot arrived.* That inference is sound only while the
bot's observation of joins has been unbroken, so it is bounded twice:

  * by a WINDOW after the bot add (`spam.pre_existing_trust_window_hours`), and
  * by COVERAGE CONTINUITY — the heartbeat below proves the process was
    watching joins with no gap; a restart, a hang or a deploy breaks it, and
    the next beat RESETS `continuous_since` to itself.

Trust is reversible: the grant records `trust_source='pre_existing'` and
`trusted_until`, and `is_trusted_member` refuses an expired grant.

Joins are written BEFORE the service message is deleted, so the record
survives the message being removed from the chat.
"""

import logging
from datetime import UTC, datetime, timedelta

from ..common.utils import load_config
from .models import GroupStatus
from .postgres_connection import get_pool

logger = logging.getLogger(__name__)

DEFAULT_PRE_EXISTING_TRUST_WINDOW_HOURS = 72
DEFAULT_COVERAGE_BEAT_SECONDS = 60
DEFAULT_COVERAGE_GAP_SECONDS = 300

TRUST_SOURCE_PROBATION = "probation"
TRUST_SOURCE_ADMIN = "admin"
TRUST_SOURCE_PRE_EXISTING = "pre_existing"

JOIN_SOURCE_SERVICE_MESSAGE = "service_message"
JOIN_SOURCE_CHAT_MEMBER = "chat_member"

MEMBER_JOINS_DDL = """
CREATE TABLE IF NOT EXISTS member_joins (
    group_id BIGINT NOT NULL,
    member_id BIGINT NOT NULL,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL DEFAULT 'service_message',
    PRIMARY KEY (group_id, member_id)
)
"""

COVERAGE_STATE_DDL = """
CREATE TABLE IF NOT EXISTS coverage_state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    last_beat_at TIMESTAMPTZ NOT NULL,
    continuous_since TIMESTAMPTZ NOT NULL
)
"""

MEMBER_JOINS_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_member_joins_joined_at ON member_joins(joined_at)
"""

# One ALTER per column: a single `ALTER TABLE` with two `ADD COLUMN` clauses is
# rejected by SQLite and by the test harness's IF-NOT-EXISTS emulation, which
# rewrites one clause per statement.
GROUPS_BOT_ADDED_AT_DDL = """
ALTER TABLE groups ADD COLUMN IF NOT EXISTS bot_added_at TIMESTAMPTZ
"""

GROUPS_BOT_REMOVED_AT_DDL = """
ALTER TABLE groups ADD COLUMN IF NOT EXISTS bot_removed_at TIMESTAMPTZ
"""

GROUPS_MIGRATED_TO_DDL = """
ALTER TABLE groups ADD COLUMN IF NOT EXISTS migrated_to_group_id BIGINT
"""

APPROVED_MEMBERS_TRUST_SOURCE_DDL = """
ALTER TABLE approved_members ADD COLUMN IF NOT EXISTS trust_source TEXT
"""

APPROVED_MEMBERS_TRUSTED_UNTIL_DDL = """
ALTER TABLE approved_members ADD COLUMN IF NOT EXISTS trusted_until TIMESTAMPTZ
"""

# The admin roster (#130). `group_administrators` becomes the roster its name
# claims — every Telegram admin of every moderated group — with two flags that
# keep its three jobs apart, so no existing reader changes meaning:
#   is_registered  the link carries billing + notification rights (bot-add,
#                  payment restore). FALSE = roster-only: never billed, never
#                  DM'd, not one of the user's groups.
#   is_admin_now   Telegram currently lists this user as an admin. This is the
#                  moderation skip, and the only predicate that may skip.
# Defaults are the safe values, so the migration alone changes nothing.
GROUP_ADMIN_IS_REGISTERED_DDL = """
ALTER TABLE group_administrators ADD COLUMN IF NOT EXISTS is_registered BOOLEAN NOT NULL DEFAULT TRUE
"""

GROUP_ADMIN_IS_ADMIN_NOW_DDL = """
ALTER TABLE group_administrators ADD COLUMN IF NOT EXISTS is_admin_now BOOLEAN NOT NULL DEFAULT TRUE
"""

# Has the user ever talked to the bot. NULL = never. `initialize_new_admin`
# keys the welcome + initial credits on this, not on row existence, because
# the roster prefill creates `administrators` rows for people who have never
# opened the bot — without it they would be silently denied both.
ADMINISTRATORS_ACTIVATED_AT_DDL = """
ALTER TABLE administrators ADD COLUMN IF NOT EXISTS activated_at TIMESTAMPTZ
"""

# Backfill: everyone already registered activated the bot by definition, or
# their credits ran to zero and a NULL would read as "never activated" and
# grant a second 100 credits on their next /start. Idempotent by predicate.
ADMINISTRATORS_ACTIVATED_AT_BACKFILL = """
UPDATE administrators SET activated_at = created_at WHERE activated_at IS NULL
"""

def _utcnow() -> datetime:
    return datetime.now(UTC)

def _as_datetime(value) -> datetime | None:
    """Coerce a DB value to an aware UTC datetime, or None if unreadable.

    asyncpg returns `datetime`; the SQLite test adapter returns the stored
    text. A naive value is read as UTC. An unreadable value returns None, and
    every caller below treats None as "not proven" — fail closed.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

def get_pre_existing_trust_window_hours() -> float:
    """Grant window AND trust horizon, measured from `groups.bot_added_at`."""
    return float(
        load_config()
        .get("spam", {})
        .get("pre_existing_trust_window_hours", DEFAULT_PRE_EXISTING_TRUST_WINDOW_HOURS)
    )

def get_coverage_beat_seconds() -> int:
    """Heartbeat cadence."""
    return int(
        load_config()
        .get("spam", {})
        .get("coverage_beat_seconds", DEFAULT_COVERAGE_BEAT_SECONDS)
    )

def get_coverage_gap_seconds() -> int:
    """Maximum gap between beats; a longer gap breaks continuity."""
    return int(
        load_config()
        .get("spam", {})
        .get("coverage_gap_seconds", DEFAULT_COVERAGE_GAP_SECONDS)
    )

async def ensure_trust_tables(conn) -> None:
    """Create the trust tables, their index and the added columns. Idempotent."""
    await conn.execute(MEMBER_JOINS_DDL)
    await conn.execute(COVERAGE_STATE_DDL)
    await conn.execute(MEMBER_JOINS_INDEX_DDL)
    await conn.execute(GROUPS_BOT_ADDED_AT_DDL)
    await conn.execute(GROUPS_BOT_REMOVED_AT_DDL)
    await conn.execute(GROUPS_MIGRATED_TO_DDL)
    await conn.execute(APPROVED_MEMBERS_TRUST_SOURCE_DDL)
    await conn.execute(APPROVED_MEMBERS_TRUSTED_UNTIL_DDL)
    await conn.execute(GROUP_ADMIN_IS_REGISTERED_DDL)
    await conn.execute(GROUP_ADMIN_IS_ADMIN_NOW_DDL)
    await conn.execute(ADMINISTRATORS_ACTIVATED_AT_DDL)
    await conn.execute(ADMINISTRATORS_ACTIVATED_AT_BACKFILL)

async def record_join(
    group_id: int, member_id: int, source: str = JOIN_SOURCE_SERVICE_MESSAGE
) -> bool:
    """Record that `member_id` was observed joining `group_id`. True if new.

    First observation wins: a redelivered service message, or the same join
    seen by both the service-message handler and `@dp.chat_member`, does not
    move `joined_at`.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO member_joins (group_id, member_id, source)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            RETURNING member_id
            """,
            group_id,
            member_id,
            source,
        )
        return row is not None

async def has_join_record(group_id: int, member_id: int) -> bool:
    """True if this sender was ever observed joining this group."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            """
            SELECT EXISTS(
                SELECT 1 FROM member_joins
                WHERE group_id = $1 AND member_id = $2
            )
            """,
            group_id,
            member_id,
        )
        return bool(exists)

async def record_bot_added(group_id: int) -> None:
    """Stamp the bot add instant and clear any stale removal.

    The group row must already exist — `_handle_bot_added` upserts it via
    `update_group_admins` before calling this.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE groups
            SET bot_added_at = NOW(), bot_removed_at = NULL
            WHERE group_id = $1
            """,
            group_id,
        )

async def record_bot_removed(group_id: int) -> None:
    """Stamp the removal instant AND retire the row.

    The removal event is the one observation that needs no sweep to confirm:
    the bot is no longer in the chat, so the group is `left` from that instant
    (D6a). Retiring here is what stops the next startup roster sweep from
    re-probing a chat we already know we are not in.

    `$N` appears in ascending text order below because the SQLite test
    adapter rewrites placeholders positionally (same trap as _retire_group).

    `bot_added_at` is untouched, and a re-add clears `bot_removed_at` via
    `record_bot_added` while `update_group_admins` clears it too (D6b) — the
    stamp means "removed at", not "ever removed".
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE groups
            SET bot_removed_at = NOW(), status = $1
            WHERE group_id = $2
            """,
            GroupStatus.LEFT.value,
            group_id,
        )

async def get_group_bot_state(group_id: int) -> dict | None:
    """`{bot_added_at, bot_removed_at}` for the group, or None if no row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT bot_added_at, bot_removed_at FROM groups WHERE group_id = $1
            """,
            group_id,
        )
    return dict(row) if row is not None else None

async def beat_coverage(gap_seconds: int | None = None) -> datetime:
    """Record a heartbeat and return `continuous_since` after the beat.

    The FIRST beat establishes continuity at itself. A beat that arrives more
    than `gap_seconds` after the previous one is a COVERAGE GAP — the process
    was not watching joins in between — so `continuous_since` is reset to the
    current beat. That is what makes a restart, a hang or a deploy break
    continuity without any separate boot signal.
    """
    threshold = get_coverage_gap_seconds() if gap_seconds is None else gap_seconds
    now = _utcnow()
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_beat_at, continuous_since FROM coverage_state WHERE id = 1"
        )
        if row is None:
            await conn.execute(
                """
                INSERT INTO coverage_state (id, last_beat_at, continuous_since)
                VALUES (1, $1, $2)
                """,
                now,
                now,
            )
            return now

        last_beat = _as_datetime(row["last_beat_at"])
        continuous_since = _as_datetime(row["continuous_since"])
        gapped = last_beat is None or (now - last_beat).total_seconds() > threshold

        if gapped or continuous_since is None:
            await conn.execute(
                """
                UPDATE coverage_state
                SET last_beat_at = $1, continuous_since = $2
                WHERE id = 1
                """,
                now,
                now,
            )
            return now

        await conn.execute(
            "UPDATE coverage_state SET last_beat_at = $1 WHERE id = 1", now
        )
        return continuous_since

async def coverage_continuous_since() -> datetime | None:
    """Instant from which join observation has been unbroken, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT continuous_since FROM coverage_state WHERE id = 1"
        )
    return _as_datetime(value)

async def get_member_trust_row(group_id: int, member_id: int) -> dict | None:
    """`{moderation_event_count, trust_source, trusted_until}`, or None."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT moderation_event_count, trust_source, trusted_until
            FROM approved_members
            WHERE group_id = $1 AND member_id = $2
            """,
            group_id,
            member_id,
        )
    return dict(row) if row is not None else None

async def grant_pre_existing_trust(
    group_id: int, member_id: int, window_hours: float | None = None
) -> None:
    """Grant reversible pre-existing trust, expiring at the window horizon."""
    hours = (
        get_pre_existing_trust_window_hours()
        if window_hours is None
        else window_hours
    )
    until = _utcnow() + timedelta(hours=hours)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO approved_members (
                group_id, member_id, moderation_event_count,
                trust_source, trusted_until
            )
            VALUES ($1, $2, $3, 'pre_existing', $4)
            ON CONFLICT (group_id, member_id) DO UPDATE
            SET trust_source = 'pre_existing',
                trusted_until = EXCLUDED.trusted_until,
                moderation_event_count = EXCLUDED.moderation_event_count
            """,
            group_id,
            member_id,
            _probation_min_events(),
            until,
        )

def _probation_min_events() -> int:
    """Local import keeps `group_operations` free of a module-level cycle."""
    from .group_operations import get_probation_min_events

    return get_probation_min_events()

async def is_pre_existing_candidate(group_id: int, member_id: int) -> bool:
    """True only when the negative inference is sound for this sender.

    Every leg fails closed — a missing row, an unreadable instant, a removed
    bot, an expired window, a coverage gap or a prior join all return False:

    1. the bot is present and has an add instant;
    2. now is inside the window after that add;
    3. coverage has been continuous since before the add;
    4. no join event was ever observed for this sender;
    5. the sender has no prior moderation row.
    """
    bot = await get_group_bot_state(group_id)
    if not bot or bot.get("bot_added_at") is None:
        return False
    if bot.get("bot_removed_at") is not None:
        return False

    added = _as_datetime(bot["bot_added_at"])
    if added is None:
        return False
    if _utcnow() > added + timedelta(hours=get_pre_existing_trust_window_hours()):
        return False

    since = await coverage_continuous_since()
    if since is None or since > added:
        return False

    if await has_join_record(group_id, member_id):
        return False
    return await get_member_trust_row(group_id, member_id) is None
