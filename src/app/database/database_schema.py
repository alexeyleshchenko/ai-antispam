import asyncpg


async def drop_and_create_database(system_conn: asyncpg.Connection, db_name: str):
    """Drop and recreate the database with specific locale and encoding"""
    try:
        # Terminate all connections to the target database
        await system_conn.execute(
            """
            SELECT pg_terminate_backend(pg_stat_activity.pid)
            FROM pg_stat_activity
            WHERE pg_stat_activity.datname = $1
            AND pid <> pg_backend_pid()
        """,
            db_name,
        )

        # Drop and recreate database with specific locale and encoding using template0
        await system_conn.execute(f"DROP DATABASE IF EXISTS {db_name}")
        await system_conn.execute(
            f"""
            CREATE DATABASE {db_name}
            WITH TEMPLATE template0
            ENCODING 'UTF8'
            LC_COLLATE = 'en_US.UTF-8'
            LC_CTYPE = 'en_US.UTF-8'
        """
        )

        # Check that encoding is set to UTF-8
        encoding = await system_conn.fetchval(
            "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname = $1",
            db_name,
        )
        assert encoding == "UTF8"

    except Exception as e:
        raise RuntimeError(f"Failed to recreate database: {e}") from e


async def create_schema(conn: asyncpg.Connection):
    """Create tables, indexes and procedures for the database"""
    try:
        # Create tables
        await conn.execute(
            """
            -- Cleanup legacy stats table
            DROP TABLE IF EXISTS stats;

            DO $$ BEGIN
                CREATE TYPE moderation_mode AS ENUM (
                    'notify', 'delete', 'delete_silent'
                );
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;

            -- Administrators table
            CREATE TABLE IF NOT EXISTS administrators (
                admin_id BIGINT PRIMARY KEY,
                username VARCHAR(255),
                credits INTEGER DEFAULT 0 CHECK (credits >= 0),
                moderation_mode moderation_mode NOT NULL DEFAULT 'notify',
                is_active BOOLEAN DEFAULT true,
                language_code VARCHAR(10),
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_active TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                credits_depleted_at TIMESTAMPTZ,
                low_balance_warned_at TIMESTAMPTZ,
                depletion_day_1_warned_at TIMESTAMPTZ,
                depletion_day_6_warned_at TIMESTAMPTZ
            );

            -- Groups table
            CREATE TABLE IF NOT EXISTS groups (
                group_id BIGINT PRIMARY KEY,
                title VARCHAR(255),
                username VARCHAR(255),
                moderation_enabled BOOLEAN DEFAULT true,
                linked_channel_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_active TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                no_rights_detected_at TIMESTAMPTZ
            );

            -- Group administrators mapping
            CREATE TABLE IF NOT EXISTS group_administrators (
                group_id BIGINT REFERENCES groups(group_id) ON DELETE CASCADE,
                admin_id BIGINT REFERENCES administrators(admin_id) ON DELETE CASCADE,
                PRIMARY KEY (group_id, admin_id)
            );

            -- Approved members
            CREATE TABLE IF NOT EXISTS approved_members (
                group_id BIGINT REFERENCES groups(group_id) ON DELETE CASCADE,
                member_id BIGINT NOT NULL,
                approved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                moderation_event_count INT NOT NULL DEFAULT 0,
                PRIMARY KEY (group_id, member_id)
            );

            -- Message history
            CREATE TABLE IF NOT EXISTS message_history (
                id SERIAL PRIMARY KEY,
                admin_id BIGINT REFERENCES administrators(admin_id) ON DELETE CASCADE,
                role VARCHAR(50) NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- Spam examples
            CREATE TABLE IF NOT EXISTS spam_examples (
                id SERIAL PRIMARY KEY,
                admin_id BIGINT REFERENCES administrators(admin_id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                name VARCHAR(255),
                bio TEXT,
                score INTEGER NOT NULL,
                linked_channel_fragment TEXT,
                stories_context TEXT,
                reply_context TEXT,
                account_signals_context TEXT,
                confirmed BOOLEAN DEFAULT true,
                chat_id BIGINT,
                message_id INTEGER,
                effective_user_id BIGINT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- Transaction history
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                admin_id BIGINT REFERENCES administrators(admin_id) ON DELETE CASCADE,
                amount INTEGER NOT NULL,
                type VARCHAR(50) NOT NULL,
                description TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            -- Message lookup cache for forwarded message resolution (TTL 7 days)
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
            );
        """
        )
    except Exception as e:
        raise RuntimeError(f"Failed to create tables: {e}") from e

    try:
        # Create indexes
        await conn.execute(
            """
            -- Administrators indexes
            CREATE INDEX IF NOT EXISTS idx_administrators_username ON administrators(username);
            CREATE INDEX IF NOT EXISTS idx_administrators_credits ON administrators(credits);

            -- Groups indexes
            CREATE INDEX IF NOT EXISTS idx_groups_moderation ON groups(moderation_enabled);
            CREATE INDEX IF NOT EXISTS idx_groups_last_active ON groups(last_active);

            -- Message history indexes
            CREATE INDEX IF NOT EXISTS idx_message_history_admin ON message_history(admin_id);
            CREATE INDEX IF NOT EXISTS idx_message_history_created ON message_history(created_at);

            -- Spam examples indexes
            CREATE INDEX IF NOT EXISTS idx_spam_examples_admin ON spam_examples(admin_id);
            CREATE INDEX IF NOT EXISTS idx_spam_examples_text ON spam_examples(text);
            CREATE INDEX IF NOT EXISTS idx_spam_examples_score ON spam_examples(score);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_spam_examples_pending_lookup
                ON spam_examples (chat_id, message_id) WHERE confirmed = false;
            CREATE INDEX IF NOT EXISTS idx_spam_examples_confirmed
                ON spam_examples (confirmed) WHERE confirmed = true;

            -- Transaction indexes
            CREATE INDEX IF NOT EXISTS idx_transactions_admin ON transactions(admin_id);
            CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type);
            CREATE INDEX IF NOT EXISTS idx_transactions_created ON transactions(created_at);
        """
        )
    except Exception as e:
        raise RuntimeError(f"Failed to create indexes: {e}") from e

    # Migrations for existing databases (ADD COLUMN IF NOT EXISTS is idempotent)
    try:
        await conn.execute(
            "ALTER TABLE groups ADD COLUMN IF NOT EXISTS username VARCHAR(255)"
        )
        await conn.execute(
            "ALTER TABLE groups ADD COLUMN IF NOT EXISTS linked_channel_id BIGINT"
        )
    except Exception as e:
        raise RuntimeError(f"Failed to run migrations: {e}") from e

    await create_procedures(conn)


async def create_procedures(conn: asyncpg.Connection):
    """Create stored procedures for the database"""
    try:
        # Create stored procedures
        await conn.execute(
            """
            -- Update group administrators
            CREATE OR REPLACE PROCEDURE update_group_admins(
                p_group_id BIGINT,
                p_admin_ids BIGINT[],
                p_initial_credits INTEGER
            )
            LANGUAGE plpgsql
            AS $$
            BEGIN
                -- Filter out bot accounts (negative IDs indicate channels/bots)
                CREATE TEMP TABLE temp_admin_ids AS
                SELECT admin_id
                FROM unnest(p_admin_ids) AS admin_id
                WHERE admin_id > 0;  -- Only positive IDs (users)

                -- Ensure admins exist in administrators table
                INSERT INTO administrators (admin_id, username, credits, created_at, last_active)
                SELECT
                    admin_id,
                    'unknown',
                    p_initial_credits,
                    NOW(),
                    NOW()
                FROM temp_admin_ids
                ON CONFLICT (admin_id) DO NOTHING;

                -- Create/update group
                INSERT INTO groups (
                    group_id,
                    moderation_enabled,
                    created_at,
                    last_active
                ) VALUES (
                    p_group_id,
                    TRUE,
                    NOW(),
                    NOW()
                )
                ON CONFLICT (group_id) DO UPDATE SET
                    last_active = NOW();

                -- Update group administrators
                DELETE FROM group_administrators
                WHERE group_id = p_group_id;

                INSERT INTO group_administrators (group_id, admin_id)
                SELECT p_group_id, admin_id
                FROM temp_admin_ids;

                -- Clean up temp table
                DROP TABLE temp_admin_ids;
            END;
            $$;

            -- Process successful payment (без рефералов)
            CREATE OR REPLACE PROCEDURE process_successful_payment(
                p_admin_id BIGINT,
                p_stars_amount INTEGER
            )
            LANGUAGE plpgsql
            AS $$
            BEGIN
                -- Add credits to the user
                INSERT INTO administrators (admin_id, credits, created_at, last_active)
                VALUES (p_admin_id, p_stars_amount, NOW(), NOW())
                ON CONFLICT (admin_id) DO UPDATE
                SET credits = administrators.credits + p_stars_amount,
                    last_active = NOW(),
                    credits_depleted_at = NULL,
                    low_balance_warned_at = NULL;

                -- Record payment transaction
                INSERT INTO transactions (
                    admin_id,
                    amount,
                    type,
                    description
                ) VALUES (
                    p_admin_id,
                    p_stars_amount,
                    'payment',
                    'Stars purchase'
                );

                -- Enable moderation in all user's groups
                UPDATE groups g
                SET moderation_enabled = true,
                last_active = NOW()
                FROM group_administrators ga
                WHERE g.group_id = ga.group_id
                AND ga.admin_id = p_admin_id;
            END;
            $$;
            """
        )
    except Exception as e:
        raise RuntimeError(f"Failed to create stored procedures: {e}") from e


async def truncate_all_tables(conn: asyncpg.Connection):
    """Truncate all tables in the database efficiently"""
    # Get all table names in a single query
    table_names = await conn.fetchval(
        """
        SELECT string_agg(quote_ident(tablename), ', ')
        FROM pg_tables
        WHERE schemaname = 'public'
        """
    )

    if table_names:
        # Truncate all tables in a single statement
        await conn.execute(f"TRUNCATE TABLE {table_names} CASCADE")
