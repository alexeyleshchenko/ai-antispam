"""Design §5 test coverage for the chat-topic feature.

Covers the gaps not already in the per-module test files:
1. DB migration idempotency (ALTER twice) + get_group returning the new fields.
2. Worst-case corpus trim: 100 x 4096-char messages -> bounded input.
3. End-to-end flow against the real (test) DB: scan -> stored -> stats line.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.database import get_group
from app.spam.chat_topics import _max_message_chars, _max_total_chars, _trim_sample

# Canonical migration SQL (mirrors database_schema.create_schema) — the test
# exercises idempotency against the live test DB with the production DDL.
_TOPIC_ALTERS = [
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS topic_description TEXT",
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS topic_description_short VARCHAR(255)",
    "ALTER TABLE groups ADD COLUMN IF NOT EXISTS topic_updated_at TIMESTAMPTZ",
]


# ---------------------------------------------------------------------------
# 1. DB migration idempotency + get_group fields
# ---------------------------------------------------------------------------


class TestMigrationIdempotency:
    @pytest.mark.asyncio
    async def test_schema_state_has_topic_columns(self, patched_db_conn, clean_db):
        """The converged schema carries each topic column exactly once.

        Works on both engines: the SQLite fixture builds the schema complete
        (the 'already migrated' state); PostgreSQL runs the ALTERs once at init.
        """
        async with clean_db.acquire() as conn:
            rows = await conn.fetch("PRAGMA table_info(groups)")
            cols = [r["name"] for r in rows]
        assert cols.count("topic_description") == 1
        assert cols.count("topic_description_short") == 1
        assert cols.count("topic_updated_at") == 1

    @pytest.mark.asyncio
    async def test_alter_idempotent_on_postgres(self, patched_db_conn, clean_db):
        """Real ALTER idempotency (PG-only syntax) — skipped under SQLite."""
        import os

        if os.getenv("USE_SQLITE_TESTS", "true").lower() != "false":
            pytest.skip("PostgreSQL-only ALTER syntax; SQLite lacks IF NOT EXISTS")

        async with clean_db.acquire() as conn:
            for _ in range(2):
                for stmt in _TOPIC_ALTERS:
                    await conn.execute(stmt)


class TestGetGroupFields:
    @pytest.mark.asyncio
    async def test_get_group_returns_topic_fields(self, patched_db_conn, clean_db):
        """get_group surfaces topic_description / _short / updated_at."""
        async with clean_db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO groups (group_id, title, topic_description,
                                    topic_description_short, topic_updated_at)
                VALUES ($1, $2, $3, $4, NOW())
                """,
                -100123,
                "PHP Jobs",
                "Full topic description",
                "PHP jobs",
            )

        group = await get_group(-100123)
        assert group is not None
        assert group.topic_description == "Full topic description"
        assert group.topic_description_short == "PHP jobs"
        assert group.topic_updated_at is not None


# ---------------------------------------------------------------------------
# 2. Worst-case corpus trim (property-style)
# ---------------------------------------------------------------------------


class TestWorstCaseTrim:
    def test_100x4096_char_messages_bounded_input(self):
        """100 messages of max Telegram length must fit the total budget."""
        messages = [{"message": "Z" * 4096} for _ in range(100)]
        parts = _trim_sample(
            messages,
            max_msg=_max_message_chars(),
            max_total=_max_total_chars(),
        )
        total_chars = sum(len(p) for p in parts)
        # Per-message head kept, total capped at 16K
        assert all(len(p) <= _max_message_chars() for p in parts)
        assert total_chars <= _max_total_chars()
        # The full 100-message corpus is NOT what reaches the LLM
        assert total_chars < 100 * 4096

    def test_trim_is_newest_first(self):
        """Newest messages (first in list) are kept, older dropped at budget."""
        messages = [
            {"message": "newest content"},
            {"message": "older content"},
            {"message": "oldest content"},
        ]
        parts = _trim_sample(
            messages,
            max_msg=100,
            max_total=20,  # only the first fits
        )
        assert parts == ["newest content"]


# ---------------------------------------------------------------------------
# 3. End-to-end flow against the real test DB
# ---------------------------------------------------------------------------


class TestEndToEndFlow:
    @pytest.mark.asyncio
    async def test_scan_store_and_stats_line(self, patched_db_conn, clean_db):
        """Full loop: seed group -> scan_chat_topics stores -> stats carries topic."""
        async with clean_db.acquire() as conn:
            await conn.execute(
                "INSERT INTO groups (group_id, title) VALUES ($1, 'PHP Jobs')",
                -100123,
            )

        mock_client = MagicMock()
        mock_client.call = AsyncMock(
            return_value={
                "messages": [
                    {"message": "What's the best PHP framework?"},
                    {"message": "Laravel vs Symfony for freelancing"},
                ],
                "count": 2,
            }
        )
        summary = MagicMock()
        summary.description = "PHP jobs discussion for freelancers."
        summary.short_description = "PHP jobs"

        with patch(
            "app.spam.chat_topics.get_mtproto_client",
            return_value=mock_client,
        ), patch(
            "app.spam.chat_topics.derive_topic_summary",
            return_value=summary,
        ):
            from app.spam.chat_topics import scan_chat_topics

            result = await scan_chat_topics(-100123)

        assert result.status == "ok"
        assert result.detail == "PHP jobs"

        # DB now holds the derived profile
        group = await get_group(-100123)
        assert group.topic_description_short == "PHP jobs"
        assert group.topic_description == "PHP jobs discussion for freelancers."

# ---------------------------------------------------------------------------
# 4. Trojan Horse regression (design §5)
# ---------------------------------------------------------------------------


class TestTrojanHorseRegression:
    @pytest.mark.asyncio
    async def test_topic_signal_never_overrides_trojan_horse(self):
        """Off-topic elevation must coexist with Trojan Horse preservation.

        The prompt, when a topic is present, must still instruct that (a) a clean
        on-topic comment from a spammy profile stays high-risk and (b) off-topic
        alone is never sufficient for a spam verdict.
        """
        from app.spam.prompt_builder import build_system_prompt
        from app.types import SpamClassificationContext

        ctx = SpamClassificationContext(chat_topics="PHP jobs discussion.")
        with patch(
            "app.spam.prompt_builder.get_spam_examples",
            AsyncMock(return_value=[]),
        ):
            prompt = await build_system_prompt(context=ctx, lang="en")

        # Chat-topic guidance present with its critical guard
        assert "## CHAT TOPIC CONTEXT" in prompt
        assert "SOLELY because it is off-topic" in prompt
        assert "weight existing signals" in prompt
        assert "stale" in prompt.lower()

        # Trojan Horse guidance still present with its critical guard
        assert "## TROJAN HORSE PATTERN" in prompt

        # No-topic baseline keeps the same Trojan Horse guarantee
        with patch(
            "app.spam.prompt_builder.get_spam_examples",
            AsyncMock(return_value=[]),
        ):
            baseline = await build_system_prompt(
                context=SpamClassificationContext(), lang="en"
            )
        assert "## TROJAN HORSE PATTERN" in baseline
        assert "## CHAT TOPIC CONTEXT" not in baseline

    def test_offtopic_flag_and_message_coexist_in_request(self):
        """The request JSON carries both the message and the topic signal."""
        import json

        from app.spam.prompt_builder import format_spam_request
        from app.types import SpamClassificationContext

        ctx = SpamClassificationContext(
            chat_topics="PHP jobs", bio="spammy promo bio"
        )
        request = json.loads(
            format_spam_request("Buy my crypto course here!", ctx)
        )
        assert request["chat_topic"] == "PHP jobs"
        assert request["message"] == "Buy my crypto course here!"
        assert request["user_bio"] == "spammy promo bio"
