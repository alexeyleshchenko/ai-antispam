"""#150 — the degraded-administrator hold gets a terminal exit.

#144 answers a degraded administrator (bot still an `administrator`, a right
withheld) with hold + notify. The hold is licensed by the notify: the DM tells a
human to tick one checkbox, and the checkbox repairs the group. That premise
fails for one sub-class — a group where EVERY registered admin has blocked the
bot. `notify_degraded_admin_rights` skips every inactive admin, sends nothing,
and the hold then has no terminating condition at all: the silent clock with a
silent `no_rights_detected_at` that #144 set out to remove.

This module covers ruling conditions 1-6. Condition 7 is a live post-deploy
read-back on `ai_spam_bot` and is deliberately not asserted here.

Assertions about ROWS (`status`, the clock, `bot_removed_at`, mapping counts) run
against the real DB fixtures — a mock would only restate the code under test, and
the whole point of the ruling is that the mappings survive the transition.
"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram import types

from app.background_jobs import no_rights
from app.background_jobs.no_rights import (
    leave_no_rights_groups,
    notify_degraded_admin_rights,
)
from app.database.group_operations import (
    get_groups_with_no_rights_past_grace,
    sweep_group_administrators,
)

GROUP = -1002496127723  # блатные чупани😎🐾 — the issue's worked example
BLOCKED_A = 5272438221  # the admin holding the 96 stranded credits
BLOCKED_B = 5272438222
REACHABLE = 5272438223

@pytest.fixture(autouse=True)
def _no_protected_channels_by_default():
    """Same default as `test_no_rights.py`: no group is a protected channel.

    c6 patches this itself, inside its own `with` block, to prove that #145's
    guard still precedes the #150 branch.
    """
    with (
        patch(
            "app.background_jobs.no_rights.get_protected_channel_ids",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.background_jobs.no_rights.get_discussion_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        yield

def _bot_user() -> types.User:
    return types.User(id=999, is_bot=True, first_name="bot")

def _real_administrator(*, can_delete: bool, can_restrict: bool):
    """A real aiogram administrator, so the isinstance branch is production's."""
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

def _past_grace(days: int = 8) -> str:
    """A clock stamp SQLite's `datetime()` parses (no offset suffix)."""
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

async def _seed(
    clean_db,
    *,
    group_id: int = GROUP,
    admins: list[tuple[int, bool]],
    approved: int = 0,
    clock_days_ago: int | None = 8,
    status: str = "active",
    linked_channel_id: int | None = None,
) -> None:
    """A group row past grace, with `admins` registered and `approved` members."""
    clock = None if clock_days_ago is None else _past_grace(clock_days_ago)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO groups (group_id, title, status, moderation_enabled, "
            "no_rights_detected_at, linked_channel_id) "
            "VALUES ($1, 'g', $2, 1, $3, $4)",
            group_id,
            status,
            clock,
            linked_channel_id,
        )
        for admin_id, is_active in admins:
            await conn.execute(
                "INSERT INTO administrators (admin_id, is_active) VALUES ($1, $2)",
                admin_id,
                1 if is_active else 0,
            )
            await conn.execute(
                "INSERT INTO group_administrators (group_id, admin_id) "
                "VALUES ($1, $2)",
                group_id,
                admin_id,
            )
        for i in range(approved):
            await conn.execute(
                "INSERT INTO approved_members (group_id, member_id) VALUES ($1, $2)",
                group_id,
                5000 + i,
            )

async def _group_row(clean_db, group_id: int = GROUP):
    async with clean_db.acquire() as conn:
        return await conn.fetchrow(
            "SELECT status, no_rights_detected_at, bot_removed_at FROM groups "
            "WHERE group_id = $1",
            group_id,
        )

async def _mapping_counts(clean_db, group_id: int = GROUP) -> tuple[int, int]:
    """(group_administrators rows, approved_members rows) for the group."""
    async with clean_db.acquire() as conn:
        admins = await conn.fetchval(
            "SELECT COUNT(*) FROM group_administrators WHERE group_id = $1", group_id
        )
        approved = await conn.fetchval(
            "SELECT COUNT(*) FROM approved_members WHERE group_id = $1", group_id
        )
    return admins, approved

def _bot_mock(*, member_count=3):
    """The Telegram surface `leave_no_rights_groups` reads, degraded on purpose."""
    mock_bot = MagicMock()
    mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
    mock_bot.get_chat = AsyncMock(
        return_value=MagicMock(title="блатные чупани", username=None)
    )
    mock_bot.get_chat_member = AsyncMock(
        return_value=_real_administrator(can_delete=True, can_restrict=False)
    )
    if isinstance(member_count, Exception):
        mock_bot.get_chat_member_count = AsyncMock(side_effect=member_count)
    else:
        mock_bot.get_chat_member_count = AsyncMock(return_value=member_count)
    mock_bot.leave_chat = AsyncMock()
    return mock_bot

# --- close condition 1: every admin blocked → terminal pause, mappings intact --

@pytest.mark.asyncio
async def test_every_admin_blocked_pauses_and_keeps_mappings(
    patched_db_conn, clean_db, caplog
):
    """c1: two admins, both inactive → paused, clock NULL, mappings UNCHANGED."""
    await _seed(clean_db, admins=[(BLOCKED_A, False), (BLOCKED_B, False)], approved=2)
    before = await _mapping_counts(clean_db)

    mock_bot = _bot_mock(member_count=3)  # a human is still there: no leave
    with (
        patch("app.background_jobs.no_rights.bot", mock_bot),
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
        patch("app.background_jobs.no_rights.send_admin_dm", new_callable=AsyncMock),
        caplog.at_level(logging.WARNING, logger="app.background_jobs.no_rights"),
    ):
        await leave_no_rights_groups()

    row = await _group_row(clean_db)
    assert row["status"] == "paused"
    assert row["no_rights_detected_at"] is None
    # #144's whole point: the mappings are what a pause exists to preserve.
    assert await _mapping_counts(clean_db) == before == (2, 2)
    mock_cleanup.assert_not_called()
    mock_bot.leave_chat.assert_not_awaited()
    # The transition must be visible to an operator, and must NAME the group.
    assert any(
        r.levelno == logging.WARNING
        and str(GROUP) in r.getMessage()
        and "EVERY registered admin is unreachable" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]

@pytest.mark.asyncio
async def test_empty_admin_set_keeps_the_old_hold(patched_db_conn, clean_db):
    """Settled design: an EMPTY registered-admin set is NOT this class.

    "Nobody is registered" is a data gap, not a statement that every human has
    blocked the bot; #144's notify already warns "no known admin to tell". The
    hold must stay exactly as it was.
    """
    await _seed(clean_db, admins=[], approved=1)

    with (
        patch("app.background_jobs.no_rights.bot", _bot_mock()),
        patch(
            "app.background_jobs.no_rights.pause_group_preserving_data",
            new_callable=AsyncMock,
        ) as mock_pause,
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ) as mock_clear,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
    ):
        await leave_no_rights_groups()

    row = await _group_row(clean_db)
    assert row["status"] == "active"
    assert row["no_rights_detected_at"] is not None
    mock_pause.assert_not_awaited()
    mock_clear.assert_not_awaited()
    mock_cleanup.assert_not_called()

# --- close condition 2: one reachable admin keeps #144 intact ----------------

@pytest.mark.asyncio
async def test_one_reachable_admin_keeps_the_hold(patched_db_conn, clean_db):
    """c2: one inactive + one active admin → still active, clock kept, one DM."""
    await _seed(clean_db, admins=[(BLOCKED_A, False), (REACHABLE, True)], approved=1)

    with patch(
        "app.background_jobs.no_rights.send_admin_dm",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_send:
        sent = await notify_degraded_admin_rights(
            [{"group_id": GROUP, "title": "g", "missing": "Ban users"}]
        )

    # The existing skip, asserted rather than assumed: only the reachable admin
    # is a recipient, and the inactive one is not DM'd at all.
    assert sent == 1
    assert [c.args[0] for c in mock_send.call_args_list] == [REACHABLE]

    with (
        patch("app.background_jobs.no_rights.bot", _bot_mock()),
        patch(
            "app.background_jobs.no_rights.pause_group_preserving_data",
            new_callable=AsyncMock,
        ) as mock_pause,
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ) as mock_clear,
    ):
        await leave_no_rights_groups()

    row = await _group_row(clean_db)
    assert row["status"] == "active"
    assert row["no_rights_detected_at"] is not None
    mock_pause.assert_not_awaited()
    mock_clear.assert_not_awaited()

# --- close condition 3: terminal by construction -----------------------------

@pytest.mark.asyncio
async def test_second_run_is_a_no_op(patched_db_conn, clean_db):
    """c3: the pause alone removes the row from BOTH populations.

    `get_groups_with_no_rights_past_grace` and `sweep_group_administrators` both
    filter `status = 'active'`, so one transition is all this class ever gets —
    the same one-shot property #145's branch relies on.
    """
    await _seed(clean_db, admins=[(BLOCKED_A, False), (BLOCKED_B, False)], approved=1)

    with patch("app.background_jobs.no_rights.bot", _bot_mock(member_count=2)):
        await leave_no_rights_groups()

    assert await get_groups_with_no_rights_past_grace(7) == []
    sweep = await sweep_group_administrators()
    assert sweep["total"] == 0
    assert sweep["synced"] == 0

    with (
        patch("app.background_jobs.no_rights.bot", _bot_mock(member_count=2)),
        patch(
            "app.background_jobs.no_rights.pause_group_preserving_data",
            new_callable=AsyncMock,
        ) as mock_pause,
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ) as mock_clear,
    ):
        await leave_no_rights_groups()  # second run: candidate list is empty

    mock_pause.assert_not_awaited()
    mock_clear.assert_not_awaited()
    assert (await _group_row(clean_db))["status"] == "paused"

# --- close condition 4: no destructive call ----------------------------------

@pytest.mark.asyncio
async def test_terminal_branch_is_never_destructive(patched_db_conn, clean_db):
    """c4: no `perform_complete_group_cleanup`, and `bot_removed_at` stays NULL.

    The bot never leaves in the pause-only case, so a `bot_removed_at` stamp
    would be a false record — that is why `retire_group` is wrong here too.
    """
    await _seed(clean_db, admins=[(BLOCKED_A, False)], approved=2)
    mock_bot = _bot_mock(member_count=1)

    with (
        patch("app.background_jobs.no_rights.bot", mock_bot),
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
        patch("app.background_jobs.no_rights.send_admin_dm", new_callable=AsyncMock),
    ):
        await leave_no_rights_groups()

    mock_cleanup.assert_not_awaited()
    row = await _group_row(clean_db)
    assert row["status"] == "paused"
    assert row["bot_removed_at"] is None
    # Even in the sole-member case, where the bot DOES leave, the mappings are
    # untouched: the leave is a Telegram call, not a cleanup.
    assert await _mapping_counts(clean_db) == (1, 2)
    # The destructive siblings are not imported into the module at all, so this
    # branch cannot reach them however it is later edited.
    assert not hasattr(no_rights, "cleanup_group_data")
    assert not hasattr(no_rights, "retire_group")

# --- close condition 5: the sole-member leave, fail-safe ---------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("probe", "should_leave"),
    [
        (1, True),  # the bot alone → nobody for the mappings to serve → leave
        (2, False),  # a human remains → blocking the bot is still repairable
        (42, False),
        ("1", False),  # unreadable answer (not an int) → pause-only
        (RuntimeError("Too Many Requests"), False),  # the read raises → pause-only
    ],
)
async def test_sole_member_leave_is_fail_safe(probe, should_leave):
    """c5: leave ONLY on an exact count of 1, and only AFTER the pause.

    The predicate is patched here so this test isolates the leave probe; c1/c3
    prove the predicate itself against real rows.
    """
    order: list[str] = []

    async def _pause(*args, **kwargs):
        order.append("pause")
        return True

    async def _leave(*args, **kwargs):
        order.append("leave")

    mock_bot = _bot_mock(member_count=probe)
    mock_bot.leave_chat = AsyncMock(side_effect=_leave)

    with (
        patch("app.background_jobs.no_rights.bot", mock_bot),
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace",
            new_callable=AsyncMock,
            return_value=[GROUP],
        ),
        patch(
            "app.background_jobs.no_rights._every_registered_admin_is_unreachable",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "app.background_jobs.no_rights.pause_group_preserving_data",
            new_callable=AsyncMock,
            side_effect=_pause,
        ) as mock_pause,
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ) as mock_clear,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
    ):
        await leave_no_rights_groups()

    # The pause is applied in EVERY case, including a raising member count.
    mock_pause.assert_awaited_once_with(GROUP, reason="no_rights_no_reachable_admin")
    mock_clear.assert_awaited_once_with(GROUP)
    mock_cleanup.assert_not_called()

    if should_leave:
        assert order == ["pause", "leave"]
        mock_bot.leave_chat.assert_awaited_once_with(GROUP)
    else:
        assert order == ["pause"]
        mock_bot.leave_chat.assert_not_awaited()

# --- close condition 6: #145's guard still wins ------------------------------

@pytest.mark.asyncio
async def test_protected_channel_never_reaches_the_branch(clean_db, caplog):
    """c6: a group satisfying BOTH predicates pauses as a PROTECTED CHANNEL.

    The protected-channel `continue` precedes the membership probe, so the
    unreachable-admin branch — and the sole-member leave it can reach — is never
    consulted for a channel whose leave would end a discussion group's
    protection.
    """
    await _seed(clean_db, admins=[(BLOCKED_A, False)], approved=1)  # satisfies #150
    mock_bot = _bot_mock()
    mock_bot.get_chat_member = AsyncMock()

    with (
        patch("app.background_jobs.no_rights.bot", mock_bot),
        patch(
            "app.background_jobs.no_rights.get_protected_channel_ids",
            new_callable=AsyncMock,
            return_value=[GROUP],  # and satisfies #145
        ),
        patch(
            "app.background_jobs.no_rights.get_discussion_group_id",
            new_callable=AsyncMock,
            return_value=-1002021099836,
        ),
        patch(
            "app.background_jobs.no_rights._every_registered_admin_is_unreachable",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_predicate,
        patch(
            "app.background_jobs.no_rights.pause_group_preserving_data",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_pause,
        patch(
            "app.background_jobs.no_rights.clear_no_rights_detected_at",
            new_callable=AsyncMock,
        ) as mock_clear,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
        caplog.at_level(logging.WARNING, logger="app.background_jobs.no_rights"),
    ):
        await leave_no_rights_groups()

    # Ordering, asserted rather than assumed: the guard short-circuits first.
    mock_predicate.assert_not_awaited()
    mock_bot.get_chat_member.assert_not_awaited()
    mock_bot.get_chat_member_count.assert_not_awaited()
    mock_bot.leave_chat.assert_not_awaited()
    mock_cleanup.assert_not_called()
    # ... and the disposition is #145's, not #150's.
    mock_pause.assert_awaited_once_with(GROUP, reason="no_rights_protected_channel")
    mock_clear.assert_awaited_once_with(GROUP)
    assert any("NOT leaving protected channel" in r.getMessage() for r in caplog.records)
    assert not any(
        "EVERY registered admin is unreachable" in r.getMessage()
        for r in caplog.records
    )
