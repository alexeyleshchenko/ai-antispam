"""The #108 and #130 columns must land on a database that PREDATES them.

`CREATE TABLE IF NOT EXISTS` does not alter a table that already exists, so a
deployed DB is upgraded by the ALTER path — the same statements the boot hook
runs. This is the positive control for that path: build a LEGACY SQLite DB in
the pre-migration shape, run the migration, assert the new columns and tables
appear. Without the migration the assertions go red.
"""

import aiosqlite
import pytest

from app.database.trust_operations import (
    ADMINISTRATORS_ACTIVATED_AT_BACKFILL,
    ensure_trust_tables,
)
from tests.conftest import SQLiteConnectionAdapter, create_sqlite_schema

# The pre-#108 shape: no bot_added_at/bot_removed_at on groups, no
# trust_source/trusted_until on approved_members, and neither new table.
# The pre-#130 shape for the two tables the admin roster adds columns to.
LEGACY_DDL = (
    """
    CREATE TABLE groups (
        group_id INTEGER PRIMARY KEY,
        title TEXT,
        moderation_enabled BOOLEAN DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE approved_members (
        group_id INTEGER,
        member_id INTEGER,
        approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        moderation_event_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (group_id, member_id)
    )
    """,
    """
    CREATE TABLE administrators (
        admin_id INTEGER PRIMARY KEY,
        username TEXT,
        credits INTEGER DEFAULT 0 CHECK (credits >= 0),
        moderation_mode TEXT NOT NULL DEFAULT 'notify',
        is_active BOOLEAN DEFAULT 1,
        language_code TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        credits_depleted_at TIMESTAMP,
        low_balance_warned_at TIMESTAMP,
        depletion_day_1_warned_at TIMESTAMP,
        depletion_day_6_warned_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE group_administrators (
        group_id INTEGER,
        admin_id INTEGER,
        PRIMARY KEY (group_id, admin_id),
        FOREIGN KEY (group_id) REFERENCES groups(group_id),
        FOREIGN KEY (admin_id) REFERENCES administrators(admin_id)
    )
    """,
)

# The columns #108 and #130 add, by table.
ADDED_COLUMNS = {
    "groups": {"bot_added_at", "bot_removed_at"},
    "approved_members": {"trust_source", "trusted_until"},
    "administrators": {"activated_at"},
    "group_administrators": {"is_registered", "is_admin_now"},
}


# The two tables the legacy fixture models in full. `groups` and
# `approved_members` are deliberately partial there (later migrations add more
# columns), so their fresh-vs-migrated sets are compared only for the columns
# this migration adds.
FULLY_MODELLED = {"administrators", "group_administrators"}


async def _columns(conn, table: str) -> set[str]:
    rows = await conn.fetch(f"PRAGMA table_info({table})")
    return {row[1] for row in rows}


async def _tables(conn) -> set[str]:
    rows = await conn.fetch("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


async def _legacy_conn() -> tuple[aiosqlite.Connection, SQLiteConnectionAdapter]:
    raw = await aiosqlite.connect(":memory:")
    conn = SQLiteConnectionAdapter(raw)
    for statement in LEGACY_DDL:
        await conn.execute(statement)
    return raw, conn


@pytest.mark.asyncio
async def test_migration_upgrades_a_legacy_db():
    """ensure_trust_tables adds every new column and table in place."""
    raw, conn = await _legacy_conn()
    try:
        # Pre-condition (neuter control): the legacy DB lacks all of them, so
        # the assertions below can only pass if the migration did the work.
        for table, columns in ADDED_COLUMNS.items():
            assert not columns & await _columns(conn, table), table
        assert not {"member_joins", "coverage_state"} & await _tables(conn)

        await ensure_trust_tables(conn)

        for table, columns in ADDED_COLUMNS.items():
            assert columns <= await _columns(conn, table), table
        assert {"member_joins", "coverage_state"} <= await _tables(conn)
    finally:
        await raw.close()


@pytest.mark.asyncio
async def test_migrated_db_matches_a_fresh_install():
    """The ALTER path and the CREATE path converge on the same column set.

    A column added to only one of them is invisible until a deployment runs on
    a database older than the other path — this is what catches that.
    """
    fresh_raw = await aiosqlite.connect(":memory:")
    legacy_raw, legacy = await _legacy_conn()
    try:
        fresh = SQLiteConnectionAdapter(fresh_raw)
        await create_sqlite_schema(fresh)
        await ensure_trust_tables(legacy)

        for table in FULLY_MODELLED:
            assert await _columns(fresh, table) == await _columns(legacy, table), table
    finally:
        await fresh_raw.close()
        await legacy_raw.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent():
    """A second run changes nothing — the boot hook runs on every start."""
    raw, conn = await _legacy_conn()
    try:
        await ensure_trust_tables(conn)
        await ensure_trust_tables(conn)

        for table, columns in ADDED_COLUMNS.items():
            assert columns <= await _columns(conn, table), table
    finally:
        await raw.close()


@pytest.mark.asyncio
async def test_activated_at_backfill_and_its_idempotency():
    """A pre-existing user is backfilled once, and a second run changes 0 rows.

    Without the backfill every registered user reads as never-activated, and
    `initialize_new_admin` would grant them a second 100 credits.
    """
    raw, conn = await _legacy_conn()
    try:
        await conn.execute(
            "INSERT INTO administrators (admin_id, credits, created_at) "
            "VALUES (7001, 0, '2026-01-02 03:04:05')"
        )

        await ensure_trust_tables(conn)

        row = await conn.fetchrow(
            "SELECT activated_at FROM administrators WHERE admin_id = 7001"
        )
        assert row is not None and row[0] is not None
        assert str(row[0]).startswith("2026-01-02 03:04:05")

        # The second run's predicate matches nothing: a measured 0 rows, not
        # an assumed one.
        assert await conn.execute(ADMINISTRATORS_ACTIVATED_AT_BACKFILL) == "UPDATE 0"
    finally:
        await raw.close()
