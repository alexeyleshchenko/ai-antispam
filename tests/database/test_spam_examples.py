import pytest
from unittest.mock import patch

from app.database import (
    add_spam_example,
    confirm_pending_example_as_not_spam,
    confirm_pending_example_as_spam,
    get_spam_examples,
    insert_pending_spam_example,
)


@pytest.mark.asyncio
async def test_get_spam_examples_common(patched_db_conn, clean_db):
    """Test getting common spam examples (without admin_ids)"""
    async with clean_db.acquire():
        # Add test example
        example_data = {"text": "spam text", "score": 80}
        await add_spam_example(text=example_data["text"], score=example_data["score"])

        # Get examples without admin_ids
        result = await get_spam_examples()

        # Verify
        assert len(result) == 1
        assert result[0]["text"] == "spam text"
        assert result[0]["score"] == 80


@pytest.mark.asyncio
async def test_get_spam_examples_with_admin(patched_db_conn, clean_db):
    """Test getting both common and admin-specific spam examples"""
    async with clean_db.acquire() as conn:
        admin_id = 12345

        # First ensure admin exists in administrators table
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits)
            VALUES ($1, 'testadmin', 100)
            ON CONFLICT DO NOTHING
        """,
            admin_id,
        )

        # Add common example
        common_example = {"text": "common spam", "score": 80}
        await add_spam_example(
            text=common_example["text"], score=common_example["score"]
        )

        # Add user-specific example
        user_example = {"text": "user spam", "score": 90}
        await add_spam_example(
            text=user_example["text"], score=user_example["score"], admin_id=admin_id
        )

        # Get all examples for the admin
        result = await get_spam_examples([admin_id])

        # Verify
        assert len(result) == 2
        assert any(ex["text"] == "common spam" for ex in result)
        assert any(ex["text"] == "user spam" for ex in result)


@pytest.mark.asyncio
async def test_get_spam_examples_with_multiple_admins(patched_db_conn, clean_db):
    """Test getting spam examples from multiple admins"""
    async with clean_db.acquire() as conn:
        admin_ids = [12345, 67890]

        # Ensure admins exist in administrators table
        for admin_id in admin_ids:
            await conn.execute(
                """
                INSERT INTO administrators (admin_id, username, credits)
                VALUES ($1, 'testadmin', 100)
                ON CONFLICT DO NOTHING
            """,
                admin_id,
            )

        # Add common example
        common_example = {"text": "common spam", "score": 80}
        await add_spam_example(
            text=common_example["text"], score=common_example["score"]
        )

        # Add examples for each admin
        examples = [
            {"text": "admin1 spam", "score": 90, "admin_id": admin_ids[0]},
            {"text": "admin2 spam", "score": 85, "admin_id": admin_ids[1]},
        ]

        for example in examples:
            await add_spam_example(
                text=example["text"],
                score=example["score"],
                admin_id=example["admin_id"],
            )

        # Get examples for both admins
        result = await get_spam_examples(admin_ids)

        # Verify
        assert len(result) == 3  # Common example + 2 admin-specific examples
        assert any(ex["text"] == "common spam" for ex in result)
        assert any(ex["text"] == "admin1 spam" for ex in result)
        assert any(ex["text"] == "admin2 spam" for ex in result)


@pytest.mark.asyncio
async def test_get_spam_examples_ham_spam_proportion(patched_db_conn, clean_db):
    """Test that examples respect config ham/spam ratio, most recent first."""
    async with clean_db.acquire() as conn:
        # Add 5 ham and 15 spam (config: limit=20, ham_ratio=0.25, spam_ratio=0.75 -> 5 ham, 15 spam)
        for i in range(5):
            await add_spam_example(text=f"ham {i}", score=-100)
        for i in range(15):
            await add_spam_example(text=f"spam {i}", score=100)

        with patch("app.database.spam_examples.load_config") as mock_load:
            mock_load.return_value = {
                "spam": {
                    "examples_limit": 20,
                    "examples_ham_ratio": 0.25,  # spam_ratio derived as 0.75
                }
            }
            result = await get_spam_examples()

        ham = [r for r in result if r["score"] < 0]
        spam = [r for r in result if r["score"] > 0]
        assert len(ham) == 5  # 25% of 20
        assert len(spam) == 15  # 75% of 20
        assert len(result) == 20


@pytest.mark.asyncio
async def test_add_spam_example_common(patched_db_conn, clean_db):
    """Test adding a common spam example"""
    async with clean_db.acquire() as conn:
        example_data = {
            "text": "Buy cheap products!",
            "score": 90,
            "name": "Spammer",
            "bio": "Professional marketer",
        }

        # Add example without admin_id
        result = await add_spam_example(
            text=example_data["text"],
            score=example_data["score"],
            name=example_data["name"],
            bio=example_data["bio"],
        )

        # Verify
        assert result is True

        # Check if example was added
        examples = await get_spam_examples()
        assert len(examples) == 1
        assert examples[0]["text"] == example_data["text"]
        assert examples[0]["score"] == example_data["score"]
        assert examples[0]["name"] == example_data["name"]
        assert examples[0]["bio"] == example_data["bio"]


@pytest.mark.asyncio
async def test_add_spam_example_user_specific(patched_db_conn, clean_db):
    """Test adding an admin-specific spam example"""
    async with clean_db.acquire() as conn:
        admin_id = 12345

        # First ensure admin exists in administrators table
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits)
            VALUES ($1, 'testadmin', 100)
            ON CONFLICT DO NOTHING
        """,
            admin_id,
        )

        example_data = {
            "text": "Buy cheap products!",
            "score": 90,
            "name": "Spammer",
            "bio": "Professional marketer",
        }

        # Add example with admin_id
        result = await add_spam_example(
            text=example_data["text"],
            score=example_data["score"],
            name=example_data["name"],
            bio=example_data["bio"],
            admin_id=admin_id,
        )

        # Verify
        assert result is True

        # Check if example was added
        examples = await get_spam_examples([admin_id])
        assert len(examples) == 1
        assert examples[0]["text"] == example_data["text"]


@pytest.mark.asyncio
async def test_add_spam_example_duplicate(patched_db_conn, clean_db):
    """Test adding a duplicate spam example"""
    async with clean_db.acquire() as conn:
        example_data = {
            "text": "Buy cheap products!",
            "score": 90,
            "name": "Spammer",
        }

        # Add first example
        await add_spam_example(
            text=example_data["text"],
            score=example_data["score"],
            name=example_data["name"],
        )

        # Add duplicate with different score
        result = await add_spam_example(
            text=example_data["text"],
            score=95,
            name=example_data["name"],
        )

        # Verify old example was updated
        assert result is True
        examples = await get_spam_examples()
        assert len(examples) == 1
        assert examples[0]["score"] == 95


@pytest.mark.asyncio
async def test_add_spam_example_with_context_fields(patched_db_conn, clean_db):
    """Test adding and retrieving spam examples with context fields"""
    async with clean_db.acquire() as conn:
        admin_id = 12345

        # First ensure admin exists in administrators table
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits)
            VALUES ($1, 'testadmin', 100)
            ON CONFLICT DO NOTHING
        """,
            admin_id,
        )

        # Test data with context fields
        example_data = {
            "text": "Test message with context",
            "score": 85,
            "name": "Test User",
            "bio": "Test bio",
            "stories_context": "Story content here",
            "reply_context": "Original reply message",
            "account_signals_context": "photo_age=2mo",
        }

        # Add example with context fields
        result = await add_spam_example(
            text=example_data["text"],
            score=example_data["score"],
            name=example_data["name"],
            bio=example_data["bio"],
            admin_id=admin_id,
            stories_context=example_data["stories_context"],
            reply_context=example_data["reply_context"],
            account_signals_context=example_data["account_signals_context"],
        )

        # Verify addition succeeded
        assert result is True

        # Retrieve examples
        examples = await get_spam_examples([admin_id])

        # Verify we have one example
        assert len(examples) == 1
        example = examples[0]

        # Verify all fields including context fields
        assert example["text"] == example_data["text"]
        assert example["score"] == example_data["score"]
        assert example["name"] == example_data["name"]
        assert example["bio"] == example_data["bio"]
        assert example["stories_context"] == example_data["stories_context"]
        assert example["reply_context"] == example_data["reply_context"]
        assert (
            example["account_signals_context"]
            == example_data["account_signals_context"]
        )


@pytest.mark.asyncio
async def test_add_spam_example_with_empty_context_markers(patched_db_conn, clean_db):
    """Test adding spam examples with '[EMPTY]' markers for checked-but-empty context"""
    async with clean_db.acquire() as conn:
        admin_id = 67890

        # First ensure admin exists in administrators table
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits)
            VALUES ($1, 'testadmin2', 100)
            ON CONFLICT DO NOTHING
        """,
            admin_id,
        )

        # Test data with some empty context markers
        example_data = {
            "text": "Message with empty context",
            "score": 70,
            "name": "Another User",
            "bio": "Another bio",
            "stories_context": "[EMPTY]",  # Checked but no stories
            "reply_context": "Some reply content",  # Found reply context
            "account_signals_context": "photo_age=unknown",  # Checked but no age info
        }

        # Add example with mixed context states
        result = await add_spam_example(
            text=example_data["text"],
            score=example_data["score"],
            name=example_data["name"],
            bio=example_data["bio"],
            admin_id=admin_id,
            stories_context=example_data["stories_context"],
            reply_context=example_data["reply_context"],
            account_signals_context=example_data["account_signals_context"],
        )

        # Verify addition succeeded
        assert result is True

        # Retrieve examples
        examples = await get_spam_examples([admin_id])

        # Verify we have one example
        assert len(examples) == 1
        example = examples[0]

        # Verify context fields with markers
        assert example["stories_context"] == "[EMPTY]"
        assert example["reply_context"] == "Some reply content"
        assert example["account_signals_context"] == "photo_age=unknown"


@pytest.mark.asyncio
async def test_insert_pending_spam_example(patched_db_conn, clean_db):
    """Test inserting a pending spam example"""
    async with clean_db.acquire() as conn:
        admin_id = 12345
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits)
            VALUES ($1, 'testadmin', 100)
            ON CONFLICT DO NOTHING
        """,
            admin_id,
        )

        pending_id = await insert_pending_spam_example(
            chat_id=100,
            message_id=50,
            effective_user_id=999,
            text="Not spam message",
            name="User",
            bio="Bio",
        )

        assert pending_id > 0

        # Pending rows should not appear in get_spam_examples
        examples = await get_spam_examples([admin_id])
        assert len(examples) == 0


@pytest.mark.asyncio
async def test_confirm_pending_example_as_not_spam(patched_db_conn, clean_db):
    """Test confirming a pending spam example"""
    async with clean_db.acquire() as conn:
        admin_id = 12345
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits)
            VALUES ($1, 'testadmin', 100)
            ON CONFLICT DO NOTHING
        """,
            admin_id,
        )

        pending_id = await insert_pending_spam_example(
            chat_id=200, message_id=60, effective_user_id=888, text="Safe message"
        )

        row = await confirm_pending_example_as_not_spam(pending_id, admin_id)

        assert row is not None
        assert row["chat_id"] == 200
        assert row["message_id"] == 60
        assert row["effective_user_id"] == 888

        # After confirm, example should appear in get_spam_examples
        examples = await get_spam_examples([admin_id])
        assert len(examples) == 1
        assert examples[0]["text"] == "Safe message"


@pytest.mark.asyncio
async def test_confirm_pending_example_as_not_spam_not_found(patched_db_conn, clean_db):
    """Test confirm returns None when pending record not found"""
    row = await confirm_pending_example_as_not_spam(99999, 12345)
    assert row is None


@pytest.mark.asyncio
async def test_confirm_pending_example_as_not_spam_already_confirmed(
    patched_db_conn, clean_db
):
    """Test confirm discriminates a duplicate callback (row exists, already confirmed)"""
    async with clean_db.acquire() as conn:
        admin_id = 12345
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits)
            VALUES ($1, 'testadmin', 100)
            ON CONFLICT DO NOTHING
        """,
            admin_id,
        )

        pending_id = await insert_pending_spam_example(
            chat_id=200, message_id=60, effective_user_id=888, text="Safe message"
        )

        # First confirm succeeds (normal path)
        first = await confirm_pending_example_as_not_spam(pending_id, admin_id)
        assert first is not None
        assert first["chat_id"] == 200

        # Second confirm (duplicate callback) → already_confirmed discriminator
        second = await confirm_pending_example_as_not_spam(pending_id, admin_id)
        assert second == {"already_confirmed": True, "admin_id": admin_id}


@pytest.mark.asyncio
async def test_get_spam_examples_excludes_pending(patched_db_conn, clean_db):
    """Test that get_spam_examples excludes pending (unconfirmed) rows"""
    async with clean_db.acquire() as conn:
        admin_id = 12345
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits)
            VALUES ($1, 'testadmin', 100)
            ON CONFLICT DO NOTHING
        """,
            admin_id,
        )

        # Add confirmed example
        await add_spam_example(text="Confirmed spam", score=80)

        # Add pending via insert_pending_spam_example
        await insert_pending_spam_example(
            chat_id=1, message_id=1, effective_user_id=1, text="Pending"
        )

        examples = await get_spam_examples()
        assert len(examples) == 1
        assert examples[0]["text"] == "Confirmed spam"


@pytest.mark.asyncio
async def test_confirm_pending_example_as_spam(patched_db_conn, clean_db):
    """Test confirming a pending spam example as spam via Delete callback"""
    async with clean_db.acquire() as conn:
        admin_id = 55555
        await conn.execute(
            """
            INSERT INTO administrators (admin_id, username, credits)
            VALUES ($1, 'delete_admin', 100)
            ON CONFLICT DO NOTHING
        """,
            admin_id,
        )

        pending_id = await insert_pending_spam_example(
            chat_id=-1001503592176,
            message_id=16614,
            effective_user_id=1136677897,
            text="500 FS in Sweet Bonanza",
        )

        result = await confirm_pending_example_as_spam(
            chat_id=-1001503592176, message_id=16614, admin_id=admin_id
        )

        assert result is True

        row = await conn.fetchrow(
            "SELECT id, confirmed, admin_id, score FROM spam_examples WHERE id = $1",
            pending_id,
        )
        assert row["confirmed"]  # SQLite returns 1 for True
        assert row["admin_id"] == admin_id
        assert row["score"] == 100

        examples = await get_spam_examples([admin_id])
        assert len(examples) == 1
        assert examples[0]["text"] == "500 FS in Sweet Bonanza"
        assert examples[0]["score"] == 100


@pytest.mark.asyncio
async def test_confirm_pending_example_as_spam_not_found(patched_db_conn, clean_db):
    """Returns False when no pending row matches chat_id/message_id."""
    result = await confirm_pending_example_as_spam(
        chat_id=999999, message_id=999999, admin_id=12345
    )
    assert result is False
