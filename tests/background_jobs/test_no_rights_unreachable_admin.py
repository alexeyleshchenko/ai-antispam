"""#161 — the ORPHANED class: bot degraded, no human admin left to repair it.

#144 answers a degraded administrator (bot still an `administrator`, a right
withheld) with hold + notify. The hold is licensed by the notify: the DM tells a
human to tick one checkbox, and the checkbox repairs the group. That premise
fails when there is NO human admin left — `notify_degraded_admin_rights` has
nobody to tell, and the hold then has no terminating condition at all.

**#150 proposed a leave for the sub-class where every registered admin had
blocked the bot. #161 WITHDRAWS that leave and replaces the predicate.** Two
reasons, both HQ's: eviction is irreversible while residency of a dormant group
costs nothing measurable, and "every admin blocked" is the wrong question — a
seat can be `is_active` yet have left the group, and a seat can be inactive yet
still own the group. The right question is the one the hold needs answered:
*can a human still tick the checkbox?*

So the predicate is `has_human_admin` (≥1 `group_administrators` row that is
`is_active`, `is_admin_now`, and not the bot), and the disposition is
**stay, silent, no retry**: status-only pause, clock cleared, mappings kept,
bot remains a member, no DM and no alert. The absence is reported by the health
digest's inventory line instead, so the class is printed rather than missing.

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

def _bot_mock():
    """The Telegram surface `leave_no_rights_groups` reads, degraded on purpose.

    No `get_chat_member_count` and no `leave_chat` stub worth asserting: #161
    withdrew the leave entirely, so the job never asks how many members there
    are. `leave_chat` is still attached so a test can assert it was NOT called —
    which is the #161 regression guard.
    """
    mock_bot = MagicMock()
    mock_bot.me = AsyncMock(return_value=MagicMock(id=999, username="test_bot"))
    mock_bot.get_chat = AsyncMock(
        return_value=MagicMock(title="блатные чупани", username=None)
    )
    mock_bot.get_chat_member = AsyncMock(
        return_value=_real_administrator(can_delete=True, can_restrict=False)
    )
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

    mock_bot = _bot_mock()
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
        and "ORPHANED" in r.getMessage()
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

    with (
        patch("app.background_jobs.no_rights.bot", _bot_mock()),
        patch(
            "app.background_jobs.no_rights.send_admin_dm",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_send,
    ):
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

@pytest.mark.asyncio
async def test_notify_skips_orphaned_group(patched_db_conn, clean_db, caplog):
    """#161: every registered admin dead → NO DM. Silent to the customer.

    The group leaves the NOTIFICATION population but stays in the INVENTORY
    one: the leave job pauses it and the digest prints it, so the operator still
    sees it. A DM to a roster of blocked accounts reaches nobody.
    """
    await _seed(clean_db, admins=[(BLOCKED_A, False)], approved=1)

    with (
        patch("app.background_jobs.no_rights.bot", _bot_mock()),
        patch(
            "app.background_jobs.no_rights.send_admin_dm",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_send,
        caplog.at_level(logging.INFO, logger="app.background_jobs.no_rights"),
    ):
        sent = await notify_degraded_admin_rights(
            [{"group_id": GROUP, "title": "g", "missing": "Ban users"}]
        )

    assert sent == 0
    mock_send.assert_not_awaited()
    assert any(
        "orphaned, no DM sent" in r.getMessage() for r in caplog.records
    ), [r.getMessage() for r in caplog.records]




# --- close condition 3: terminal by construction -----------------------------

@pytest.mark.asyncio
async def test_second_run_is_a_no_op(patched_db_conn, clean_db):
    """c3: the pause alone removes the row from BOTH populations.

    `get_groups_with_no_rights_past_grace` and `sweep_group_administrators` both
    filter `status = 'active'`, so one transition is all this class ever gets —
    the same one-shot property #145's branch relies on.
    """
    await _seed(clean_db, admins=[(BLOCKED_A, False), (BLOCKED_B, False)], approved=1)

    with patch("app.background_jobs.no_rights.bot", _bot_mock()):
        await leave_no_rights_groups()

    assert await get_groups_with_no_rights_past_grace(7) == []
    sweep = await sweep_group_administrators()
    assert sweep["total"] == 0
    assert sweep["synced"] == 0

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
    mock_bot = _bot_mock()

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

# --- close condition 5: ORPHANED → stay, silent, no retry --------------------

@pytest.mark.asyncio
async def test_orphaned_group_pauses_and_stays(patched_db_conn, clean_db, caplog):
    """c5: no human admin left → pause, clear the clock, KEEP the mappings.

    The disposition is HQ's ruling on #161, verbatim: "STAY, silent, no retry."
    `bot.leave_chat` is the assertion that matters — #150 proposed exactly that
    leave and #161 withdrew it, so an unauthorised leave is the regression this
    test exists to catch. A dormant group's cost of residency is nothing;
    eviction cannot be undone.
    """
    await _seed(clean_db, admins=[(BLOCKED_A, False)], approved=2)
    before = await _mapping_counts(clean_db)
    mock_bot = _bot_mock()

    with (
        patch("app.background_jobs.no_rights.bot", mock_bot),
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace",
            new_callable=AsyncMock,
            return_value=[GROUP],
        ),
        patch(
            "app.background_jobs.no_rights.has_human_admin",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_has_human,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
        caplog.at_level(logging.WARNING, logger="app.background_jobs.no_rights"),
    ):
        await leave_no_rights_groups()

    mock_has_human.assert_awaited_once_with(GROUP, exclude_id=999)
    mock_cleanup.assert_not_awaited()
    # #161's whole point: the bot stays. #150's leave must not come back.
    mock_bot.leave_chat.assert_not_awaited()

    # The transition is asserted on the DATABASE, not on a mock of the function
    # that performs it: `pause_group_preserving_data` and
    # `clear_no_rights_detected_at` run for real here. Mocking them and then
    # reading the row would assert the mock's return value, not the branch.
    row = await _group_row(clean_db)
    assert row["status"] == "paused"
    assert row["no_rights_detected_at"] is None
    # The bot never left, so a stamp here would be a false record.
    assert row["bot_removed_at"] is None
    # Obligation 2: the departed admin's rows are EVIDENCE, not garbage.
    # One admin is seeded, so the tuple is (1, 2) — the pause keeps both.
    assert await _mapping_counts(clean_db) == before == (1, 2)
    # Silent to the CUSTOMER (no DM), printed to the OPERATOR (digest inventory).
    assert any(
        r.levelno == logging.WARNING
        and str(GROUP) in r.getMessage()
        and "ORPHANED" in r.getMessage()
        for r in caplog.records
    ), [r.getMessage() for r in caplog.records]

@pytest.mark.asyncio
async def test_a_raising_predicate_cannot_reach_the_leave(patched_db_conn, clean_db):
    """c5b: if `has_human_admin` raises, the group is held — never evicted.

    `skip_leave = True` is set BEFORE the predicate is awaited, so an unreadable
    database can only cost us the classification, never a leave. The fail-safe
    direction is deliberate: a group we failed to classify is treated like one we
    know to be repairable.
    """
    await _seed(clean_db, admins=[(BLOCKED_A, False)], approved=1)
    mock_bot = _bot_mock()

    with (
        patch("app.background_jobs.no_rights.bot", mock_bot),
        patch(
            "app.background_jobs.no_rights.get_groups_with_no_rights_past_grace",
            new_callable=AsyncMock,
            return_value=[GROUP],
        ),
        patch(
            "app.background_jobs.no_rights.has_human_admin",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ),
        patch(
            "app.background_jobs.no_rights.pause_group_preserving_data",
            new_callable=AsyncMock,
        ) as mock_pause,
        patch(
            "app.background_jobs.no_rights.perform_complete_group_cleanup",
            new_callable=AsyncMock,
        ) as mock_cleanup,
    ):
        await leave_no_rights_groups()

    mock_bot.leave_chat.assert_not_awaited()
    mock_cleanup.assert_not_awaited()
    mock_pause.assert_not_awaited()
    assert (await _group_row(clean_db))["status"] == "active"


# --- close condition 6: #145's guard still wins ------------------------------

@pytest.mark.asyncio
async def test_protected_channel_never_reaches_the_branch(clean_db, caplog):
    """c6: a group satisfying BOTH predicates pauses as a PROTECTED CHANNEL.

    The protected-channel `continue` precedes the membership probe, so the
    orphan/degraded branch — and whatever disposition it carries — is never
    consulted for a channel whose leave would end a discussion group's
    protection. `has_human_admin` is patched rather than the removed #150
    predicate, and its `assert_not_awaited()` is the ordering proof: the guard
    short-circuits before the question is ever asked.
    """
    await _seed(clean_db, admins=[(BLOCKED_A, False)], approved=1)  # satisfies #161
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
            "app.background_jobs.no_rights.has_human_admin",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_has_human,
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
    mock_has_human.assert_not_awaited()
    mock_bot.get_chat_member.assert_not_awaited()
    mock_bot.leave_chat.assert_not_awaited()
    mock_cleanup.assert_not_called()
    # ... and the disposition is #145's, not #161's.
    mock_pause.assert_awaited_once_with(GROUP, reason="no_rights_protected_channel")
    mock_clear.assert_awaited_once_with(GROUP)
    assert any("NOT leaving protected channel" in r.getMessage() for r in caplog.records)
    assert not any("ORPHANED" in r.getMessage() for r in caplog.records)