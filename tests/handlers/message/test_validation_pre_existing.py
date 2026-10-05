"""Trust-decision tests for issue #108 (pre-existing members, B+D, R5).

Three legs of the close condition are discharged here, at the handler layer:

* leg 2 — a pre-existing candidate is trusted WITHOUT an LLM call;
* leg 3 — a coverage gap closes the grant, so the sender is classified;
* leg 5 — an EDIT by a ``pre_existing``-trusted sender is classified (R5).

The predicate itself (`is_pre_existing_candidate`) is covered in
`tests/database/test_trust_operations.py`; what is under test here is the
DECISION built on it — which path may mint trust, and which path may spend it.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.database.trust_operations import beat_coverage
from src.app.handlers.message.pipeline import handle_moderated_message
from src.app.handlers.message.validation import validate_group_and_check_early_exits

CHAT_ID = -1001234
USER_ID = 555001

def _now() -> datetime:
    return datetime.now(UTC)

@pytest.fixture
def src_pool(test_pool):
    """Point the `src.*` module instance at the test pool.

    The suite imports the app twice — as `app.*` (pytest.ini sets
    `pythonpath = src`) and as `src.app.*` — and those are two distinct module
    objects with two distinct `_pool` globals. The session-wide
    `patched_db_conn` patches `app.database.postgres_connection._pool`, which
    the handler under test never reads. Without this, a DB-backed test here
    reaches for a real Postgres and dies on name resolution.
    """
    from src.app.database import postgres_connection

    original = postgres_connection._pool
    postgres_connection._pool = test_pool
    yield
    postgres_connection._pool = original

def _group(admin_ids: list[int] | None = None) -> object:
    return type(
        "Group",
        (),
        {"admin_ids": admin_ids if admin_ids is not None else [999], "moderation_enabled": True},
    )()

def _patch_group(group: object):
    return patch(
        "src.app.handlers.message.validation.get_and_check_group",
        new_callable=AsyncMock,
        return_value=(group, ""),
    )

def _patch_trust_row(row: dict | None):
    return patch(
        "src.app.handlers.message.validation.get_trusted_member_row",
        new_callable=AsyncMock,
        return_value=row,
    )

# --------------------------------------------------------------------------
# Leg 2 — a pre-existing candidate is trusted without an LLM call
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_existing_candidate_is_trusted_on_the_new_path():
    group = _group()
    with (
        _patch_group(group),
        _patch_trust_row(None),
        patch(
            "src.app.handlers.message.validation.is_pre_existing_candidate",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.app.handlers.message.validation.grant_pre_existing_trust",
            new_callable=AsyncMock,
        ) as grant,
    ):
        _, reason = await validate_group_and_check_early_exits(
            CHAT_ID, USER_ID, source="new"
        )

    assert reason == "message_trusted_member_skipped"
    grant.assert_awaited_once_with(CHAT_ID, USER_ID)

@pytest.mark.asyncio
async def test_trusted_candidate_never_reaches_the_classifier(mock_message):
    """The whole point of the grant: no LLM call for a pre-existing member.

    Real validation runs here (only the DB helpers are stubbed), so the test
    fails if the pipeline ever starts classifying before the early exit.
    """
    group = _group()
    with (
        patch(
            "src.app.handlers.message.pipeline.is_member_in_group",
            new_callable=AsyncMock,
            return_value=False,
        ),
        _patch_group(group),
        _patch_trust_row(None),
        patch(
            "src.app.handlers.message.validation.is_pre_existing_candidate",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.app.handlers.message.validation.grant_pre_existing_trust",
            new_callable=AsyncMock,
        ) as grant,
        patch(
            "src.app.handlers.message.pipeline._handle_trusted_member_exit",
            new_callable=AsyncMock,
        ),
        patch(
            "src.app.handlers.message.pipeline.classify_spam",
            new_callable=AsyncMock,
        ) as classify,
    ):
        result = await handle_moderated_message(mock_message)

    assert result == "message_trusted_member_skipped"
    grant.assert_awaited_once()
    classify.assert_not_called()

@pytest.mark.asyncio
async def test_edit_does_not_mint_a_grant():
    """A re-delivered edit must not become a second chance to mint trust."""
    group = _group()
    with (
        _patch_group(group),
        _patch_trust_row(None),
        patch(
            "src.app.handlers.message.validation.is_pre_existing_candidate",
            new_callable=AsyncMock,
            return_value=True,
        ) as candidate,
        patch(
            "src.app.handlers.message.validation.grant_pre_existing_trust",
            new_callable=AsyncMock,
        ) as grant,
    ):
        _, reason = await validate_group_and_check_early_exits(
            CHAT_ID, USER_ID, source="edit"
        )

    assert reason == ""
    candidate.assert_not_called()
    grant.assert_not_awaited()

# --------------------------------------------------------------------------
# Leg 3 — a coverage gap closes the grant
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coverage_gap_closes_the_grant(src_pool, patched_db_conn, clean_db):
    """Positive control and neutered leg in one test, same DB state.

    Healthy first (continuity predates the add) -> trusted. Then continuity is
    advanced past the add, i.e. the bot cannot say what happened at the add
    instant -> the very same sender is classified.
    """
    group = _group()
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title) VALUES ($1, $2)", CHAT_ID, "T"
        )
    await beat_coverage()
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE coverage_state SET continuous_since = $1 WHERE id = 1",
            _now() - timedelta(days=10),
        )
        await conn.execute(
            "UPDATE groups SET bot_added_at = $1 WHERE group_id = $2",
            _now() - timedelta(hours=1),
            CHAT_ID,
        )

    with _patch_group(group), patch(
        "src.app.handlers.message.validation.grant_pre_existing_trust",
        new_callable=AsyncMock,
    ) as grant:
        _, reason = await validate_group_and_check_early_exits(
            CHAT_ID, USER_ID, source="new"
        )
    assert reason == "message_trusted_member_skipped", "control: healthy state trusts"
    assert grant.await_count == 1

    # The gap: coverage only begins now, AFTER the bot was added.
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE coverage_state SET continuous_since = $1 WHERE id = 1", _now()
        )

    with _patch_group(group), patch(
        "src.app.handlers.message.validation.grant_pre_existing_trust",
        new_callable=AsyncMock,
    ) as grant2:
        _, reason = await validate_group_and_check_early_exits(
            CHAT_ID, USER_ID, source="new"
        )
    assert reason == "", "a coverage gap must leave the sender classified"
    grant2.assert_not_awaited()

# --------------------------------------------------------------------------
# Leg 5 (R5) — an edit by a pre-existing-trusted sender is classified
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_by_pre_existing_trusted_sender_is_classified():
    group = _group()
    row = {"moderation_event_count": 3, "trust_source": "pre_existing", "trusted_until": None}
    with _patch_group(group), _patch_trust_row(row):
        _, reason = await validate_group_and_check_early_exits(
            CHAT_ID, USER_ID, source="edit"
        )

    assert reason == ""

@pytest.mark.asyncio
async def test_edit_by_probation_trusted_sender_is_still_skipped():
    """R-e guard: only `pre_existing` trust is re-examined on an edit."""
    group = _group()
    row = {"moderation_event_count": 3, "trust_source": "probation", "trusted_until": None}
    with _patch_group(group), _patch_trust_row(row):
        _, reason = await validate_group_and_check_early_exits(
            CHAT_ID, USER_ID, source="edit"
        )

    assert reason == "message_trusted_member_skipped"

@pytest.mark.asyncio
async def test_new_message_by_pre_existing_trusted_sender_is_skipped():
    """The same row on the new path keeps its free pass (the R5 carve-out is edits only)."""
    group = _group()
    row = {"moderation_event_count": 3, "trust_source": "pre_existing", "trusted_until": None}
    with _patch_group(group), _patch_trust_row(row):
        _, reason = await validate_group_and_check_early_exits(
            CHAT_ID, USER_ID, source="new"
        )

    assert reason == "message_trusted_member_skipped"

@pytest.mark.asyncio
async def test_edit_by_pre_existing_trusted_sender_reaches_the_classifier(
    mock_message, mock_message_context_result
):
    """End-to-end leg 5: the edited message is actually classified."""
    group = _group()
    row = {"moderation_event_count": 3, "trust_source": "pre_existing", "trusted_until": None}
    with (
        patch(
            "src.app.handlers.message.pipeline.is_member_in_group",
            new_callable=AsyncMock,
            return_value=False,
        ),
        _patch_group(group),
        _patch_trust_row(row),
        patch(
            "src.app.handlers.message.pipeline.check_skip_channel_bot_message",
            new_callable=AsyncMock,
            return_value=(False, ""),
        ),
        patch(
            "src.app.handlers.message.pipeline.collect_message_context",
            new_callable=AsyncMock,
            return_value=mock_message_context_result,
        ),
        patch(
            "src.app.handlers.message.pipeline.classify_spam",
            new_callable=AsyncMock,
            return_value=(False, 95, "ham"),
        ) as classify,
        patch(
            "src.app.handlers.message.pipeline.process_spam_or_approve",
            new_callable=AsyncMock,
            return_value=("message_user_approved", False),
        ),
        patch(
            "src.app.handlers.message.pipeline._save_classification_lookup",
            new_callable=AsyncMock,
        ),
        patch("src.app.handlers.message.pipeline.get_root_span") as span,
    ):
        span.return_value.set_attribute = MagicMock()
        await handle_moderated_message(mock_message, source="edit")

    classify.assert_awaited_once()
