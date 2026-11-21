"""#161 — `has_human_admin`: is there still a live human behind this group?

The four fixtures below are the live rows the ruling was written against
(measured 2026-11-21), reproduced in the SQLite fixture:

  SHADOWRAZE  paused, its only roster seat is `is_active = FALSE` — the real
              orphan. MUST answer False.
  SV_CLUB     paused, admin 882155584 `is_active = TRUE` with 66 credits.
              MUST answer True: paused is not the same as abandoned.
  SV_CHAT     active, one ordinary roster seat. MUST answer True.
  CHUPANI     `left`, every `group_administrators` row deleted. MUST answer
              False.

Each test is written so it can go red: the discrimination control is the
`is_active` clause (test 2) and the roster read itself (test 3). Both were
verified red against a deliberately broken predicate before this file landed.
"""

import pytest

from app.common.telegram_errors import GROUP_ANONYMOUS_BOT_ID
from app.database import get_groups_with_human_admin, has_human_admin

SHADOWRAZE = -1003751652625  # paused; only seat is is_active = FALSE
SV_CLUB = -1002107631154  # paused; seat is_active = TRUE, 66 credits
SV_CHAT = -1002021099836  # active; ordinary seat
CHUPANI = -1002496127723  # left; roster rows all deleted
BOT_ID = 777000111  # stands in for `bot.id`, which lives outside the DB


async def _group(conn, group_id: int, status: str = "active") -> None:
    await conn.execute(
        "INSERT INTO groups (group_id, title, status) VALUES ($1, $2, $3)",
        group_id,
        f"Группа {group_id}",
        status,
    )


async def _admin(conn, admin_id: int, *, is_active: bool = True) -> None:
    await conn.execute(
        "INSERT INTO administrators (admin_id, credits, is_active) "
        "VALUES ($1, $2, $3)",
        admin_id,
        66,
        is_active,
    )


async def _seat(
    conn,
    group_id: int,
    admin_id: int,
    *,
    is_admin_now: bool = True,
    is_registered: bool = True,
) -> None:
    await conn.execute(
        "INSERT INTO group_administrators "
        "(group_id, admin_id, is_registered, is_admin_now) VALUES ($1, $2, $3, $4)",
        group_id,
        admin_id,
        is_registered,
        is_admin_now,
    )


@pytest.mark.asyncio
async def test_active_admin_in_a_paused_group_counts(patched_db_conn, clean_db):
    """SV CLUB: paused, but its admin is active — True, not 'abandoned'."""
    async with clean_db.acquire() as conn:
        await _group(conn, SV_CLUB, status="paused")
        await _admin(conn, 882155584, is_active=True)
        await _seat(conn, SV_CLUB, 882155584)

    assert await has_human_admin(SV_CLUB) is True


@pytest.mark.asyncio
async def test_inactive_admin_does_not_count(patched_db_conn, clean_db):
    """Сервер Shadowraze: the seat exists, the account is deactivated — False."""
    async with clean_db.acquire() as conn:
        await _group(conn, SHADOWRAZE, status="paused")
        await _admin(conn, 7054850510, is_active=False)
        await _seat(conn, SHADOWRAZE, 7054850510)

    assert await has_human_admin(SHADOWRAZE) is False


@pytest.mark.asyncio
async def test_group_with_no_roster_rows_does_not_count(patched_db_conn, clean_db):
    """блатные чупани: the roster rows are gone, so nothing vouches for it."""
    async with clean_db.acquire() as conn:
        await _group(conn, CHUPANI, status="left")
        await _admin(conn, 7054850510, is_active=True)

    assert await has_human_admin(CHUPANI) is False


@pytest.mark.asyncio
async def test_one_active_admin_among_two_is_enough(patched_db_conn, clean_db):
    """"At least one", not "all": a dead seat must not veto the live one."""
    async with clean_db.acquire() as conn:
        await _group(conn, SV_CHAT)
        await _admin(conn, 111, is_active=False)
        await _admin(conn, 222, is_active=True)
        await _seat(conn, SV_CHAT, 111)
        await _seat(conn, SV_CHAT, 222)

    assert await has_human_admin(SV_CHAT) is True


@pytest.mark.asyncio
async def test_demoted_seat_does_not_count(patched_db_conn, clean_db):
    """`is_admin_now = FALSE` is this codebase's "no longer in that chat"."""
    async with clean_db.acquire() as conn:
        await _group(conn, SV_CHAT)
        await _admin(conn, 333, is_active=True)
        await _seat(conn, SV_CHAT, 333, is_admin_now=False)

    assert await has_human_admin(SV_CHAT) is False


@pytest.mark.asyncio
async def test_bot_seat_alone_does_not_count(patched_db_conn, clean_db):
    """A seat belonging to the bot itself is not a human admin.

    The first assertion is the control: without `exclude_id` the same row
    counts, so the False below is the exclusion doing the work and not some
    unrelated filter.
    """
    async with clean_db.acquire() as conn:
        await _group(conn, SV_CHAT)
        await _admin(conn, BOT_ID, is_active=True)
        await _seat(conn, SV_CHAT, BOT_ID)

    assert await has_human_admin(SV_CHAT) is True
    assert await has_human_admin(SV_CHAT, exclude_id=BOT_ID) is False


@pytest.mark.asyncio
async def test_excluding_the_bot_keeps_the_human(patched_db_conn, clean_db):
    """Excluding the bot must not hide a real admin sitting next to it."""
    async with clean_db.acquire() as conn:
        await _group(conn, SV_CLUB, status="paused")
        await _admin(conn, BOT_ID, is_active=True)
        await _admin(conn, 882155584, is_active=True)
        await _seat(conn, SV_CLUB, BOT_ID)
        await _seat(conn, SV_CLUB, 882155584)

    assert await has_human_admin(SV_CLUB, exclude_id=BOT_ID) is True


@pytest.mark.asyncio
async def test_anonymous_admin_account_is_not_a_human(patched_db_conn, clean_db):
    """GROUP_ANONYMOUS_BOT_ID is a system account, never a person."""
    async with clean_db.acquire() as conn:
        await _group(conn, SV_CHAT)
        await _admin(conn, GROUP_ANONYMOUS_BOT_ID, is_active=True)
        await _seat(conn, SV_CHAT, GROUP_ANONYMOUS_BOT_ID)

    assert await has_human_admin(SV_CHAT) is False


@pytest.mark.asyncio
async def test_batch_returns_only_the_groups_with_a_human_admin(
    patched_db_conn, clean_db
):
    """The four live cases in one query: only SV_CLUB and SV_CHAT survive."""
    async with clean_db.acquire() as conn:
        await _group(conn, SHADOWRAZE, status="paused")
        await _admin(conn, 7054850510, is_active=False)
        await _seat(conn, SHADOWRAZE, 7054850510)

        await _group(conn, SV_CLUB, status="paused")
        await _admin(conn, 882155584, is_active=True)
        await _seat(conn, SV_CLUB, 882155584)

        await _group(conn, SV_CHAT)
        await _admin(conn, 444, is_active=True)
        await _seat(conn, SV_CHAT, 444)

        await _group(conn, CHUPANI, status="left")

    found = await get_groups_with_human_admin(
        [SHADOWRAZE, SV_CLUB, SV_CHAT, CHUPANI]
    )

    assert found == {SV_CLUB, SV_CHAT}


@pytest.mark.asyncio
async def test_batch_honours_the_exclusion(patched_db_conn, clean_db):
    """The batch variant carries the same bot-id exclusion as the single one."""
    async with clean_db.acquire() as conn:
        await _group(conn, SV_CHAT)
        await _admin(conn, BOT_ID, is_active=True)
        await _seat(conn, SV_CHAT, BOT_ID)

    assert await get_groups_with_human_admin([SV_CHAT]) == {SV_CHAT}
    assert await get_groups_with_human_admin([SV_CHAT], exclude_id=BOT_ID) == set()


@pytest.mark.asyncio
async def test_batch_with_no_input_returns_empty(patched_db_conn, clean_db):
    """`IN ()` is invalid SQL, so empty input must short-circuit."""
    assert await get_groups_with_human_admin([]) == set()
