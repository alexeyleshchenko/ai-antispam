"""#143 — the bot's own membership is asserted, never inferred.

`moderation_enabled` used to be verified against nothing. A group could read
`status='active'`, `moderation_enabled=true` while the bot held neither
`can_delete_messages` nor `can_restrict_members`, and every surface — the
dashboard, the health checks, the operator's own reading — called it
protected. Measured 2026-11-09: 3 of 43 moderated groups, one of them an
`administrator` with both rights false, which receives every message and so
reads healthy on any check that looks at status alone.

The probe is one request per group per sweep; the clock it drives is the
existing `no_rights_detected_at`, so the existing grace job owns the pause and
there is no second writer of `status`.
"""

from unittest.mock import AsyncMock, patch

import pytest
from aiogram import types

from app.database import group_operations
from app.database.group_operations import (
    BotRights,
    probe_bot_moderation_rights,
    reconcile_bot_rights,
)

GROUP = -1001430001


def _bot_user() -> types.User:
    return types.User(id=999, is_bot=True, first_name="bot")


def _administrator(
    *, can_delete: bool, can_restrict: bool
) -> types.ChatMemberAdministrator:
    return types.ChatMemberAdministrator(
        user=_bot_user(),
        can_be_edited=False,
        is_anonymous=False,
        can_manage_chat=True,
        can_delete_messages=can_delete,
        can_manage_video_chats=True,
        can_restrict_members=can_restrict,
        can_promote_members=False,
        can_change_info=True,
        can_invite_users=True,
        can_post_stories=False,
        can_edit_stories=False,
        can_delete_stories=False,
    )


def _probe(member) -> AsyncMock:
    """Patch the Telegram read; returns the mock so a test can read its args."""
    return patch.object(
        group_operations.bot, "get_chat_member", new=AsyncMock(return_value=member)
    )


async def _seed_group(clean_db, group_id: int = GROUP, *, clock: str | None = None):
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, status, moderation_enabled, "
            "no_rights_detected_at) VALUES ($1, 'g', 'active', 1, $2)",
            group_id,
            clock,
        )


async def _clock(clean_db, group_id: int = GROUP):
    async with clean_db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT no_rights_detected_at FROM groups WHERE group_id = $1", group_id
        )
    return row["no_rights_detected_at"]


# --- close condition 4: the probe cannot run without the bot's identity ------


@pytest.mark.asyncio
async def test_probe_asks_about_the_bot_itself():
    """The request names the BOT's own id — the whole defect was asking about others."""
    with _probe(_administrator(can_delete=True, can_restrict=True)) as mock:
        rights = await probe_bot_moderation_rights(GROUP)

    assert rights == BotRights("administrator", True, True)
    assert mock.await_args.kwargs == {
        "chat_id": GROUP,
        "user_id": group_operations.bot.id,
    }


@pytest.mark.asyncio
async def test_probe_reads_a_plain_member_as_rightless():
    """A member receives nothing to moderate, so it can never hold the rights."""
    with _probe(types.ChatMemberMember(user=_bot_user())):
        rights = await probe_bot_moderation_rights(GROUP)

    assert rights.can_moderate is False
    assert rights.missing == "not an administrator (status=member)"


@pytest.mark.asyncio
async def test_probe_names_the_missing_right():
    """The operator line must say WHICH right is absent, not that something is."""
    with _probe(_administrator(can_delete=False, can_restrict=True)):
        rights = await probe_bot_moderation_rights(GROUP)

    assert rights.can_moderate is False
    assert rights.missing == "can_delete_messages"


# --- close condition 2: each membership shape drives the clock ---------------


@pytest.mark.asyncio
async def test_administrator_without_rights_flags_the_clock(patched_db_conn, clean_db):
    """The sharpest instance: an administrator that still receives messages."""
    await _seed_group(clean_db)

    with _probe(_administrator(can_delete=False, can_restrict=False)):
        bucket = await reconcile_bot_rights(GROUP, clock_set=False)

    assert bucket == "flagged"
    assert await _clock(clean_db) is not None


@pytest.mark.asyncio
async def test_demoted_member_flags_the_clock(patched_db_conn, clean_db):
    await _seed_group(clean_db)

    with _probe(types.ChatMemberMember(user=_bot_user())):
        bucket = await reconcile_bot_rights(GROUP, clock_set=False)

    assert bucket == "flagged"
    assert await _clock(clean_db) is not None


@pytest.mark.asyncio
async def test_fully_privileged_clears_a_stale_clock(patched_db_conn, clean_db):
    """Rights restored is the restore path — the grace job must not fire later."""
    await _seed_group(clean_db, clock="2026-11-01 00:00:00")

    with _probe(_administrator(can_delete=True, can_restrict=True)):
        bucket = await reconcile_bot_rights(GROUP, clock_set=True)

    assert bucket == "cleared"
    assert await _clock(clean_db) is None


@pytest.mark.asyncio
async def test_healthy_group_with_no_clock_writes_nothing(patched_db_conn, clean_db):
    """39 of 43 groups take this path: no clock, no write, no churn."""
    await _seed_group(clean_db)

    with (
        _probe(_administrator(can_delete=True, can_restrict=True)),
        patch(
            "app.database.group_operations.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ) as mock_clear,
    ):
        bucket = await reconcile_bot_rights(GROUP, clock_set=False)

    assert bucket == "ok"
    mock_clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_sweep_does_not_churn_the_clock(patched_db_conn, clean_db):
    """`set_no_rights_detected_at` is first-detection-only; the driver must respect it."""
    await _seed_group(clean_db)

    with _probe(_administrator(can_delete=False, can_restrict=False)):
        assert await reconcile_bot_rights(GROUP, clock_set=False) == "flagged"
        first = await _clock(clean_db)
        assert await reconcile_bot_rights(GROUP, clock_set=True) == "held"
        second = await _clock(clean_db)

    assert first == second


# --- close condition 4: "we could not ask" is not "it is fine" --------------


@pytest.mark.asyncio
async def test_probe_failure_never_reads_as_health(patched_db_conn, clean_db):
    """A skipped probe must not clear a clock, and must not stay invisible."""
    await _seed_group(clean_db, clock="2026-11-01 00:00:00")

    with patch.object(
        group_operations.bot,
        "get_chat_member",
        new=AsyncMock(side_effect=RuntimeError("api down")),
    ):
        bucket = await reconcile_bot_rights(GROUP, clock_set=True)

    assert bucket == "unverified"
    # The stale clock SURVIVES: an unreadable answer is not a restoration.
    assert await _clock(clean_db) is not None


@pytest.mark.asyncio
async def test_probe_failure_does_not_invent_a_flag(patched_db_conn, clean_db):
    """Nor does it invent a defect: an unflagged group stays unflagged."""
    await _seed_group(clean_db)

    with patch.object(
        group_operations.bot,
        "get_chat_member",
        new=AsyncMock(side_effect=RuntimeError("api down")),
    ):
        bucket = await reconcile_bot_rights(GROUP, clock_set=False)

    assert bucket == "unverified"
    assert await _clock(clean_db) is None
