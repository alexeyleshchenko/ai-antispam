"""The #108 columns must land on a database that PREDATES them.

`CREATE TABLE IF NOT EXISTS` does not alter a table that already exists, so a
deployed DB is upgraded by the ALTER path — the same statements the boot hook
runs. This is the positive control for that path: build a LEGACY SQLite DB in
the pre-#108 shape, run the migration, assert the new columns and tables
appear. Without the migration the assertions go red.
"""

import aiosqlite
import pytest

from app.database.trust_operations import ensure_trust_tables
from tests.conftest import SQLiteConnectionAdapter

# The pre-#108 shape: no bot_added_at/bot_removed_at on groups, no
# trust_source/trusted_until on approved_members, and neither new table.
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
)


async def _columns(conn, table: str) -> set[str]:
    rows = await conn.fetch(f"PRAGMA table_info({table})")
    return {row[1] for row in rows}


async def _tables(conn) -> set[str]:
    rows = await conn.fetch("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row[0] for row in rows}


@pytest.mark.asyncio
async def test_migration_upgrades_a_legacy_db():
    """ensure_trust_tables adds every new column and table in place."""
    raw = await aiosqlite.connect(":memory:")
    try:
        conn = SQLiteConnectionAdapter(raw)
        for statement in LEGACY_DDL:
            await conn.execute(statement)

        # Pre-condition (neuter control): the legacy DB lacks all of them, so
        # the assertions below can only pass if the migration did the work.
        assert "bot_added_at" not in await _columns(conn, "groups")
        assert "bot_removed_at" not in await _columns(conn, "groups")
        assert "trust_source" not in await _columns(conn, "approved_members")
        assert "trusted_until" not in await _columns(conn, "approved_members")
        assert not {"member_joins", "coverage_state"} & await _tables(conn)

        await ensure_trust_tables(conn)

        assert {"bot_added_at", "bot_removed_at"} <= await _columns(conn, "groups")
        assert {"trust_source", "trusted_until"} <= await _columns(
            conn, "approved_members"
        )
        assert {"member_joins", "coverage_state"} <= await _tables(conn)
    finally:
        await raw.close()


@pytest.mark.asyncio
async def test_migration_is_idempotent():
    """A second run changes nothing — the boot hook runs on every start."""
    raw = await aiosqlite.connect(":memory:")
    try:
        conn = SQLiteConnectionAdapter(raw)
        for statement in LEGACY_DDL:
            await conn.execute(statement)

        await ensure_trust_tables(conn)
        await ensure_trust_tables(conn)

        assert {"bot_added_at", "bot_removed_at"} <= await _columns(conn, "groups")
        assert {"trust_source", "trusted_until"} <= await _columns(
            conn, "approved_members"
        )
    finally:
        await raw.close()
