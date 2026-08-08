import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import asyncio
from typing import Any

import asyncpg

# Load environment variables
from dotenv import load_dotenv

load_dotenv()
from src.app.common.utils import load_config
from src.app.database import get_pool
from src.app.database.database_schema import create_procedures, create_schema


async def create_database():
    # Connect to default database to create new one
    system_conn = await asyncpg.connect(
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASSWORD"),
        host=os.getenv("PG_HOST"),
        port=int(os.getenv("PG_PORT", "5432")),
        database="postgres",  # Connect to default database
    )

    try:
        # Check if database exists
        exists = await system_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", os.getenv("PG_DB")
        )

        if not exists:
            print(f"Creating database {os.getenv('PG_DB')}...")
            # Create new database
            await system_conn.execute(f"CREATE DATABASE {os.getenv('PG_DB')}")
            print("Database created successfully!")
        else:
            print(f"Database {os.getenv('PG_DB')} already exists.")

    finally:
        await system_conn.close()


async def migrate(conn: Any) -> list[str]:
    """
    Perform database migration operations.

    Args:
        conn (Any): The database connection object.

    Returns:
        List[str]: A list of operations performed during the migration process.
    """
    operations = []
    print("Starting database migration...")

    # Start transaction
    async with conn.transaction():
        print("Creating schema...")
        await create_schema(conn)
        operations.append("Created schema")
        print("✓ Created schema")

    print(f"Migration completed successfully. {len(operations)} operations performed.")
    return operations


async def add_context_columns_migration(conn: Any) -> list[str]:
    """
    Add context columns to existing spam_examples table.
    This migration adds stories_context, reply_context, and account_age_context columns.
    """
    operations = []
    print("Starting context columns migration...")

    async with conn.transaction():
        # Add stories_context column
        await conn.execute(
            "ALTER TABLE spam_examples ADD COLUMN IF NOT EXISTS stories_context TEXT"
        )
        operations.append("Added stories_context column")
        print("✓ Added stories_context column")

        # Add reply_context column
        await conn.execute(
            "ALTER TABLE spam_examples ADD COLUMN IF NOT EXISTS reply_context TEXT"
        )
        operations.append("Added reply_context column")
        print("✓ Added reply_context column")

        # Add account_age_context column
        await conn.execute(
            "ALTER TABLE spam_examples ADD COLUMN IF NOT EXISTS account_age_context TEXT"
        )
        operations.append("Added account_age_context column")
        print("✓ Added account_age_context column")

    print(
        f"Context columns migration completed successfully. {len(operations)} operations performed."
    )
    return operations


async def add_pending_spam_example_columns_migration(conn: Any) -> list[str]:
    """
    Add pending spam example columns: confirmed, chat_id, message_id, effective_user_id.
    """
    operations = []
    print("Starting pending spam example columns migration...")

    async with conn.transaction():
        await conn.execute(
            "ALTER TABLE spam_examples ADD COLUMN IF NOT EXISTS confirmed BOOLEAN DEFAULT true"
        )
        operations.append("Added confirmed column")
        print("✓ Added confirmed column")

        await conn.execute(
            "ALTER TABLE spam_examples ADD COLUMN IF NOT EXISTS chat_id BIGINT"
        )
        operations.append("Added chat_id column")
        print("✓ Added chat_id column")

        await conn.execute(
            "ALTER TABLE spam_examples ADD COLUMN IF NOT EXISTS message_id INTEGER"
        )
        operations.append("Added message_id column")
        print("✓ Added message_id column")

        await conn.execute(
            "ALTER TABLE spam_examples ADD COLUMN IF NOT EXISTS effective_user_id BIGINT"
        )
        operations.append("Added effective_user_id column")
        print("✓ Added effective_user_id column")

        await conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_spam_examples_pending_lookup
                ON spam_examples (chat_id, message_id) WHERE confirmed = false
            """
        )
        operations.append("Created idx_spam_examples_pending_lookup")
        print("✓ Created idx_spam_examples_pending_lookup")

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_spam_examples_confirmed
                ON spam_examples (confirmed) WHERE confirmed = true
            """
        )
        operations.append("Created idx_spam_examples_confirmed")
        print("✓ Created idx_spam_examples_confirmed")

        await conn.execute(
            """
            UPDATE spam_examples SET confirmed = true WHERE confirmed IS NULL
            """
        )
        operations.append("Backfilled confirmed for existing rows")
        print("✓ Backfilled confirmed for existing rows")

    print(
        f"Pending spam example columns migration completed successfully. {len(operations)} operations performed."
    )
    return operations


async def add_low_balance_columns_migration(conn: Any) -> list[str]:
    """
    Add credits_depleted_at and low_balance_warned_at to administrators.
    Backfill credits_depleted_at = NOW() for existing admins with credits = 0.
    """
    operations = []
    print("Starting low balance columns migration...")

    async with conn.transaction():
        await conn.execute(
            """
            ALTER TABLE administrators
            ADD COLUMN IF NOT EXISTS credits_depleted_at TIMESTAMP
            """
        )
        operations.append("Added credits_depleted_at column")
        print("✓ Added credits_depleted_at column")

        await conn.execute(
            """
            ALTER TABLE administrators
            ADD COLUMN IF NOT EXISTS low_balance_warned_at TIMESTAMP
            """
        )
        operations.append("Added low_balance_warned_at column")
        print("✓ Added low_balance_warned_at column")

        await conn.execute(
            """
            UPDATE administrators
            SET credits_depleted_at = NOW()
            WHERE credits = 0 AND credits_depleted_at IS NULL
            """
        )
        operations.append("Backfilled credits_depleted_at for existing zeros")
        print("✓ Backfilled credits_depleted_at for existing zeros")

        print("Updating process_successful_payment procedure...")
        await create_procedures(conn)
        operations.append("Updated process_successful_payment to clear depletion flags")
        print("✓ Updated process_successful_payment procedure")

    print(
        f"Low balance columns migration completed successfully. {len(operations)} operations performed."
    )
    return operations


async def add_language_code_migration(conn: Any) -> list[str]:
    """
    Add language_code column to administrators.
    Backfill: all existing admins get language_code = 'ru'.
    """
    operations = []
    print("Starting language_code column migration...")

    async with conn.transaction():
        await conn.execute(
            """
            ALTER TABLE administrators
            ADD COLUMN IF NOT EXISTS language_code VARCHAR(10)
            """
        )
        operations.append("Added language_code column")
        print("✓ Added language_code column")

        await conn.execute(
            """
            UPDATE administrators
            SET language_code = 'ru'
            WHERE language_code IS NULL
            """
        )
        operations.append("Backfilled language_code = 'ru' for existing admins")
        print("✓ Backfilled language_code = 'ru' for existing admins")

    print(
        f"Language code migration completed successfully. {len(operations)} operations performed."
    )
    return operations


async def add_message_lookup_cache_migration(conn: Any) -> list[str]:
    """
    Create message_lookup_cache table for PostgreSQL message lookup (replaces Logfire).
    """
    operations = []
    print("Starting message_lookup_cache migration...")

    async with conn.transaction():
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS message_lookup_cache (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                effective_user_id BIGINT NOT NULL,
                message_text TEXT NOT NULL,
                reply_to_text TEXT,
                stories_context TEXT,
                account_signals_context TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(chat_id, message_id)
            )
            """
        )
        operations.append("Created message_lookup_cache table")
        print("✓ Created message_lookup_cache table")

        await conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_message_lookup_text_user_created
                ON message_lookup_cache(message_text, effective_user_id, created_at)
            """
        )
        operations.append("Created idx_message_lookup_text_user_created")
        print("✓ Created idx_message_lookup_text_user_created")

    print(
        f"message_lookup_cache migration completed successfully. {len(operations)} operations performed."
    )
    return operations


async def rename_account_signals_context_migration(conn: Any) -> list[str]:
    """
    Rename account_age_context -> account_signals_context on spam_examples and message_lookup_cache.
    Idempotent: only renames when the old column exists.
    """
    operations: list[str] = []
    print("Starting account_signals_context column rename...")

    async with conn.transaction():
        for table in ("spam_examples", "message_lookup_cache"):
            exists_old = await conn.fetchval(
                """
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = $1
                  AND column_name = 'account_age_context'
                """,
                table,
            )
            if exists_old:
                await conn.execute(
                    f'ALTER TABLE "{table}" RENAME COLUMN account_age_context TO account_signals_context'
                )
                operations.append(
                    f"{table}: account_age_context -> account_signals_context"
                )
                print(f"✓ Renamed column on {table}")

    print(
        f"account_signals_context rename completed. {len(operations)} operation(s) performed."
    )
    return operations


async def add_timestamptz_migration(conn: Any) -> list[str]:
    """
    Convert all TIMESTAMP columns to TIMESTAMPTZ.
    Existing values are interpreted as UTC. Enables timezone-aware datetime from Python/asyncpg.
    """
    operations = []
    print("Starting TIMESTAMPTZ migration...")

    async with conn.transaction():
        await conn.execute("SET timezone = 'UTC'")

        # administrators
        await conn.execute(
            """
            ALTER TABLE administrators
            ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC',
            ALTER COLUMN last_active TYPE timestamptz USING last_active AT TIME ZONE 'UTC',
            ALTER COLUMN credits_depleted_at TYPE timestamptz USING credits_depleted_at AT TIME ZONE 'UTC',
            ALTER COLUMN low_balance_warned_at TYPE timestamptz USING low_balance_warned_at AT TIME ZONE 'UTC'
            """
        )
        operations.append(
            "administrators: created_at, last_active, credits_depleted_at, low_balance_warned_at"
        )
        print("✓ administrators")

        # groups
        await conn.execute(
            """
            ALTER TABLE groups
            ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC',
            ALTER COLUMN last_active TYPE timestamptz USING last_active AT TIME ZONE 'UTC'
            """
        )
        operations.append("groups: created_at, last_active")
        print("✓ groups")

        # approved_members
        await conn.execute(
            """
            ALTER TABLE approved_members
            ALTER COLUMN approved_at TYPE timestamptz USING approved_at AT TIME ZONE 'UTC'
            """
        )
        operations.append("approved_members: approved_at")
        print("✓ approved_members")

        # message_history
        await conn.execute(
            """
            ALTER TABLE message_history
            ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC'
            """
        )
        operations.append("message_history: created_at")
        print("✓ message_history")

        # spam_examples
        await conn.execute(
            """
            ALTER TABLE spam_examples
            ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC'
            """
        )
        operations.append("spam_examples: created_at")
        print("✓ spam_examples")

        # transactions
        await conn.execute(
            """
            ALTER TABLE transactions
            ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC'
            """
        )
        operations.append("transactions: created_at")
        print("✓ transactions")

        # message_lookup_cache (table may not exist if --add-message-lookup-cache not run yet)
        exists = await conn.fetchval(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'message_lookup_cache'"
        )
        if exists:
            await conn.execute(
                """
                ALTER TABLE message_lookup_cache
                ALTER COLUMN created_at TYPE timestamptz USING created_at AT TIME ZONE 'UTC'
                """
            )
            operations.append("message_lookup_cache: created_at")
            print("✓ message_lookup_cache")

    print(
        f"TIMESTAMPTZ migration completed successfully. {len(operations)} table groups updated."
    )
    return operations


async def add_is_active_column_migration(conn: Any) -> list[str]:
    """
    Ensure administrators table has the is_active column.
    Needed for broadcast scripts and cleanup logic.
    """
    operations = []
    print("Starting is_active column migration...")

    async with conn.transaction():
        await conn.execute(
            """
            ALTER TABLE administrators
            ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE
            """
        )
        operations.append("Ensured is_active column exists")
        print("✓ Ensured is_active column exists")

        await conn.execute(
            """
            UPDATE administrators
            SET is_active = TRUE
            WHERE is_active IS NULL
            """
        )
        operations.append("Backfilled is_active for existing rows")
        print("✓ Backfilled is_active for existing rows")

        await conn.execute(
            """
            ALTER TABLE administrators
            ALTER COLUMN is_active SET DEFAULT TRUE
            """
        )
        operations.append("Enforced default TRUE for is_active")
        print("✓ Enforced default TRUE for is_active")

    print(
        f"is_active column migration completed successfully. {len(operations)} operations performed."
    )
    return operations


async def add_depletion_warning_flags_migration(conn: Any) -> list[str]:
    """
    Add depletion_day_1_warned_at and depletion_day_6_warned_at to avoid sending
    duplicate day-1/day-6 warnings on each deploy or job run.
    """
    operations = []
    print("Starting depletion warning flags migration...")

    async with conn.transaction():
        await conn.execute(
            """
            ALTER TABLE administrators
            ADD COLUMN IF NOT EXISTS depletion_day_1_warned_at TIMESTAMPTZ
            """
        )
        operations.append("Added depletion_day_1_warned_at column")
        print("✓ Added depletion_day_1_warned_at column")

        await conn.execute(
            """
            ALTER TABLE administrators
            ADD COLUMN IF NOT EXISTS depletion_day_6_warned_at TIMESTAMPTZ
            """
        )
        operations.append("Added depletion_day_6_warned_at column")
        print("✓ Added depletion_day_6_warned_at column")

    print(
        f"Depletion warning flags migration completed successfully. {len(operations)} operations performed."
    )
    return operations


async def add_no_rights_column_migration(conn: Any) -> list[str]:
    """
    Add no_rights_detected_at to groups. Used for grace period before leaving
    groups where bot has no required rights.
    """
    operations = []
    print("Starting no_rights column migration...")

    async with conn.transaction():
        await conn.execute(
            """
            ALTER TABLE groups
            ADD COLUMN IF NOT EXISTS no_rights_detected_at TIMESTAMPTZ
            """
        )
        operations.append("Added no_rights_detected_at column")
        print("✓ Added no_rights_detected_at column")

    print(
        f"No rights column migration completed successfully. {len(operations)} operations performed."
    )
    return operations


async def run_context_columns_migration():
    """Run the context columns migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running context columns migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_context_columns_migration(conn)


async def run_low_balance_columns_migration():
    """Run the low balance columns migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running low balance columns migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_low_balance_columns_migration(conn)


async def run_language_code_migration():
    """Run the language_code column migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running language_code migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_language_code_migration(conn)


async def run_message_lookup_cache_migration():
    """Run the message_lookup_cache migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running message_lookup_cache migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_message_lookup_cache_migration(conn)


async def run_rename_account_signals_context_migration():
    """Rename account_age_context columns to account_signals_context."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running account_signals_context rename migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await rename_account_signals_context_migration(conn)


async def run_is_active_column_migration():
    """Run the is_active column migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running is_active column migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_is_active_column_migration(conn)


async def run_timestamptz_migration():
    """Run the TIMESTAMPTZ migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running timestamptz migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_timestamptz_migration(conn)


async def run_depletion_warning_flags_migration():
    """Run the depletion warning flags migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running depletion warning flags migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_depletion_warning_flags_migration(conn)


async def run_no_rights_column_migration():
    """Run the no_rights_detected_at column migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running no_rights column migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_no_rights_column_migration(conn)


async def add_moderation_mode_migration(conn: Any) -> list[str]:
    """
    Phase 1: CREATE TYPE moderation_mode, add column, backfill from delete_spam.
    Keeps delete_spam for rollback / old image compatibility.
    """
    operations: list[str] = []
    print("Starting moderation_mode migration (phase 1)...")

    async with conn.transaction():
        try:
            await conn.execute(
                """
                CREATE TYPE moderation_mode AS ENUM (
                    'notify', 'delete', 'delete_silent'
                )
                """
            )
            operations.append("Created moderation_mode enum type")
            print("✓ Created moderation_mode enum type")
        except asyncpg.DuplicateObjectError:
            print("✓ moderation_mode enum type already exists")

        col_exists = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'administrators'
              AND column_name = 'moderation_mode'
            """
        )
        if not col_exists:
            await conn.execute(
                """
                ALTER TABLE administrators
                ADD COLUMN moderation_mode moderation_mode NOT NULL DEFAULT 'notify'
                """
            )
            operations.append("Added moderation_mode column")
            print("✓ Added moderation_mode column")

        await conn.execute(
            """
            UPDATE administrators
            SET moderation_mode = (
                CASE WHEN delete_spam THEN 'delete'::moderation_mode
                     ELSE 'notify'::moderation_mode END
            )
            WHERE moderation_mode IS DISTINCT FROM (
                CASE WHEN delete_spam THEN 'delete'::moderation_mode
                     ELSE 'notify'::moderation_mode END
            )
            """
        )
        operations.append("Backfilled moderation_mode from delete_spam")
        print("✓ Backfilled moderation_mode from delete_spam")

    print(
        f"moderation_mode migration completed. {len(operations)} operation(s) performed."
    )
    return operations


async def drop_delete_spam_migration(conn: Any) -> list[str]:
    """Phase 3: drop legacy delete_spam column (moderation_mode remains)."""
    operations: list[str] = []
    print("Starting drop delete_spam migration (phase 3)...")

    async with conn.transaction():
        col_exists = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'administrators'
              AND column_name = 'delete_spam'
            """
        )
        if col_exists:
            await conn.execute("ALTER TABLE administrators DROP COLUMN delete_spam")
            operations.append("Dropped delete_spam column")
            print("✓ Dropped delete_spam column")
        else:
            print("✓ delete_spam column already absent")

    print(
        f"drop delete_spam migration completed. {len(operations)} operation(s) performed."
    )
    return operations


async def run_moderation_mode_migration():
    """Run moderation_mode phase-1 migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    pool = await get_pool()
    print("Running moderation_mode migration...")
    async with pool.acquire() as conn:
        await add_moderation_mode_migration(conn)


async def run_drop_delete_spam_migration():
    """Run drop delete_spam phase-3 migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    pool = await get_pool()
    print("Running drop delete_spam migration...")
    async with pool.acquire() as conn:
        await drop_delete_spam_migration(conn)


async def add_moderation_event_count_migration(conn: Any) -> list[str]:
    """
    Add moderation_event_count to approved_members and grandfather existing rows
    to probation_min_events so legacy members keep trusted skip on edits.
    """
    operations = []
    min_events = int(load_config().get("spam", {}).get("probation_min_events", 3))
    print(f"Starting moderation_event_count migration (grandfather to {min_events})...")

    async with conn.transaction():
        await conn.execute(
            """
            ALTER TABLE approved_members
            ADD COLUMN IF NOT EXISTS moderation_event_count INT NOT NULL DEFAULT 0
            """
        )
        operations.append("Added moderation_event_count column")
        print("✓ Added moderation_event_count column")

        result = await conn.execute(
            """
            UPDATE approved_members
            SET moderation_event_count = $1
            """,
            min_events,
        )
        operations.append(f"Grandfathered approved_members ({result})")
        print(f"✓ Grandfathered existing approved_members to count={min_events}")

    print(f"Moderation event count migration completed. {len(operations)} operations.")
    return operations


async def run_moderation_event_count_migration():
    """Run moderation_event_count column migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running moderation_event_count migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_moderation_event_count_migration(conn)


async def run_pending_spam_example_migration():
    """Run the pending spam example columns migration manually."""
    print("Creating database if it doesn't exist...")
    await create_database()
    print("Getting database pool...")
    pool = await get_pool()
    print("Running pending spam example columns migration...")
    async with pool.acquire() as conn:
        print("Acquired connection from pool")
        await add_pending_spam_example_columns_migration(conn)


async def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--add-context-columns":
            await run_context_columns_migration()
        elif sys.argv[1] == "--add-low-balance-columns":
            await run_low_balance_columns_migration()
        elif sys.argv[1] == "--add-is-active-column":
            await run_is_active_column_migration()
        elif sys.argv[1] == "--add-message-lookup-cache":
            await run_message_lookup_cache_migration()
        elif sys.argv[1] == "--rename-account-signals-context":
            await run_rename_account_signals_context_migration()
        elif sys.argv[1] == "--add-pending-spam-columns":
            await run_pending_spam_example_migration()
        elif sys.argv[1] == "--add-language-code":
            await run_language_code_migration()
        elif sys.argv[1] == "--add-timestamptz":
            await run_timestamptz_migration()
        elif sys.argv[1] == "--add-depletion-warning-flags":
            await run_depletion_warning_flags_migration()
        elif sys.argv[1] == "--add-no-rights-column":
            await run_no_rights_column_migration()
        elif sys.argv[1] == "--add-moderation-event-count":
            await run_moderation_event_count_migration()
        elif sys.argv[1] == "--add-moderation-mode":
            await run_moderation_mode_migration()
        elif sys.argv[1] == "--drop-delete-spam":
            await run_drop_delete_spam_migration()
        else:
            raise ValueError(f"Unknown migration flag {sys.argv[1]!r}")
    else:
        # Run full migration
        print("Creating database if it doesn't exist...")
        await create_database()
        print("Getting database pool...")
        pool = await get_pool()
        print("Migrating database...")
        async with pool.acquire() as conn:
            print("Acquired connection from pool")
            await migrate(conn)


if __name__ == "__main__":
    asyncio.run(main())
