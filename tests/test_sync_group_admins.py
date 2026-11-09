"""#130 — the roster sync reconciles our memory against Telegram's own list.

The bug this closes is measured, not hypothetical: `group_administrators` had
one production writer (the bot-add event) while `get_chat_administrators` was
called at seven sites and persisted at none, so 45 of 84 admin seats across 13
groups were invisible to `validation`. One of them was classified as spam at
0.90, had his message DELETED, could not be banned (he was an admin), and the
group's owner was charged 1★ for the review.

The tests below pin the two properties that make the sync safe to run against
production: a dry run writes NOTHING, and a dry run's numbers are the SAME
numbers `--apply` writes. Plus the two filters (bots, the anonymous-admin id)
and the rule that a demotion is a FLAG — a paying admin whose seat is gone must
keep being billed.
"""

import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.common.telegram_errors import GROUP_ANONYMOUS_BOT_ID
from app.database import group_operations
from app.database.group_operations import (
    get_group,
    sync_group_administrators,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _admin(user_id: int, *, is_bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(user=SimpleNamespace(id=user_id, is_bot=is_bot))


def _patch_admins(monkeypatch, admins: list[SimpleNamespace]) -> None:
    monkeypatch.setattr(
        group_operations.bot,
        "get_chat_administrators",
        AsyncMock(return_value=admins),
    )


async def _seed_group(conn, group_id: int, title: str = "Тишина") -> None:
    await conn.execute(
        "INSERT INTO groups (group_id, title) VALUES ($1, $2) "
        "ON CONFLICT DO NOTHING",
        group_id,
        title,
    )


async def _seed_seat(conn, group_id: int, admin_id: int, *, registered: bool) -> None:
    """A pre-existing roster row, billing or roster-only."""
    await conn.execute(
        "INSERT INTO administrators (admin_id, credits) VALUES ($1, 0) "
        "ON CONFLICT DO NOTHING",
        admin_id,
    )
    await conn.execute(
        "INSERT INTO group_administrators "
        "(group_id, admin_id, is_registered, is_admin_now) "
        "VALUES ($1, $2, $3, TRUE) ON CONFLICT DO NOTHING",
        group_id,
        admin_id,
        registered,
    )


async def _roster(clean_db, group_id: int) -> dict[int, tuple[bool, bool]]:
    async with clean_db.acquire() as conn:
        rows = await conn.fetch(
            "SELECT admin_id, is_registered, is_admin_now FROM group_administrators "
            "WHERE group_id = $1",
            group_id,
        )
    return {
        row["admin_id"]: (bool(row["is_registered"]), bool(row["is_admin_now"]))
        for row in rows
    }


async def _credits(clean_db, admin_id: int):
    async with clean_db.acquire() as conn:
        return await conn.fetchval(
            "SELECT credits FROM administrators WHERE admin_id = $1", admin_id
        )


# --- dry run is a pure read --------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(patched_db_conn, clean_db, monkeypatch):
    group_id = -1001300001
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)
        await _seed_seat(conn, group_id, 9001, registered=True)

    _patch_admins(monkeypatch, [_admin(9001), _admin(9002), _admin(9003)])

    report = await sync_group_administrators(group_id, apply=False)

    assert (report.telegram, report.known, report.new, report.demoted) == (3, 1, 2, 0)
    assert report.applied is False

    # Nothing written: one seat on disk, and no administrator rows for the two
    # strangers the read discovered.
    assert set(await _roster(clean_db, group_id)) == {9001}
    assert await _credits(clean_db, 9002) is None
    assert await _credits(clean_db, 9003) is None


@pytest.mark.asyncio
async def test_the_default_argument_is_the_dry_run(
    patched_db_conn, clean_db, monkeypatch
):
    """`sync_group_administrators(chat_id)` — with no `apply` at all — writes nothing.

    The CLI test below asserts the `--apply` help text, and that is a claim
    about a STRING. It stayed green when the function's own default was flipped
    to `apply=True`, because every other test in this file passes the flag
    explicitly: the default was asserted in prose and tested nowhere. The
    default is what a future caller who forgets the flag gets, so it is pinned
    on the behaviour — call it bare and count the rows.
    """
    group_id = -1001300002
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)
        await _seed_seat(conn, group_id, 9101, registered=True)

    _patch_admins(monkeypatch, [_admin(9101), _admin(9102)])

    report = await sync_group_administrators(group_id)

    assert report.applied is False
    assert set(await _roster(clean_db, group_id)) == {9101}
    assert await _credits(clean_db, 9102) is None

@pytest.mark.asyncio
async def test_dry_run_and_apply_report_the_same_numbers(
    patched_db_conn, clean_db, monkeypatch
):
    """A preview that counts differently from the write previews nothing."""
    group_id = -1001300002
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)
        await _seed_seat(conn, group_id, 9101, registered=True)
        await _seed_seat(conn, group_id, 9102, registered=True)

    # 9102 is no longer an admin; 9103 and 9104 are new.
    _patch_admins(monkeypatch, [_admin(9101), _admin(9103), _admin(9104)])

    preview = await sync_group_administrators(group_id, apply=False)
    written = await sync_group_administrators(group_id, apply=True)

    assert (preview.telegram, preview.known, preview.new, preview.demoted) == (
        written.telegram,
        written.known,
        written.new,
        written.demoted,
    )
    assert written.demoted == 1
    assert written.new == 2


# --- apply -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_seeds_new_seats_without_granting_credits(
    patched_db_conn, clean_db, monkeypatch
):
    """A discovered admin is roster-only: 0 credits, not activated, not billed."""
    group_id = -1001300003
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)

    _patch_admins(monkeypatch, [_admin(9201), _admin(9202)])

    report = await sync_group_administrators(group_id, apply=True)
    assert (report.telegram, report.new, report.demoted) == (2, 2, 0)

    roster = await _roster(clean_db, group_id)
    assert roster == {9201: (False, True), 9202: (False, True)}, roster

    for admin_id in (9201, 9202):
        assert await _credits(clean_db, admin_id) == 0, "prefill must not pay"

    async with clean_db.acquire() as conn:
        activated = await conn.fetchval(
            "SELECT activated_at FROM administrators WHERE admin_id = $1", 9201
        )
    assert activated is None, "prefill must not consume the welcome"


@pytest.mark.asyncio
async def test_second_apply_reports_zero_new(patched_db_conn, clean_db, monkeypatch):
    group_id = -1001300004
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)

    _patch_admins(monkeypatch, [_admin(9301), _admin(9302)])

    first = await sync_group_administrators(group_id, apply=True)
    assert first.new == 2

    second = await sync_group_administrators(group_id, apply=True)
    assert second.new == 0, "a re-run must not re-discover its own writes"
    assert second.known == 2
    assert second.demoted == 0

    assert set(await _roster(clean_db, group_id)) == {9301, 9302}


@pytest.mark.asyncio
async def test_demotion_is_a_flag_and_keeps_billing(
    patched_db_conn, clean_db, monkeypatch
):
    """A paying admin who lost the seat stops being skipped, keeps being billed."""
    group_id = -1001300005
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)
        await _seed_seat(conn, group_id, 9401, registered=True)
        await _seed_seat(conn, group_id, 9402, registered=True)

    # 9402 was demoted in Telegram; 9401 is still an admin.
    _patch_admins(monkeypatch, [_admin(9401)])

    report = await sync_group_administrators(group_id, apply=True)
    assert report.demoted == 1

    roster = await _roster(clean_db, group_id)
    assert roster[9402] == (True, False), (
        "demotion must clear is_admin_now and leave is_registered alone — "
        "deleting the row would silently stop charging the payer"
    )
    assert roster[9401] == (True, True)

    group = await get_group(group_id)
    assert 9401 in group.telegram_admin_ids
    assert 9402 not in group.telegram_admin_ids
    assert 9402 in group.admin_ids, "a demoted payer keeps their billing link"


@pytest.mark.asyncio
async def test_demoted_roster_only_seat_is_kept(patched_db_conn, clean_db, monkeypatch):
    """A roster-only seat that lost the seat is flagged, never deleted."""
    group_id = -1001300006
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)
        await _seed_seat(conn, group_id, 9501, registered=False)
        await _seed_seat(conn, group_id, 9502, registered=False)

    _patch_admins(monkeypatch, [_admin(9501)])

    await sync_group_administrators(group_id, apply=True)

    roster = await _roster(clean_db, group_id)
    assert roster == {9501: (False, True), 9502: (False, False)}, roster


# --- filters -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_bots_and_anonymous_admin_are_filtered(patched_db_conn, clean_db, monkeypatch):
    """Neither a bot nor the anonymous-admin pseudo-user is a moderatable human."""
    group_id = -1001300007
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)

    _patch_admins(
        monkeypatch,
        [
            _admin(9601),
            _admin(9602, is_bot=True),
            _admin(GROUP_ANONYMOUS_BOT_ID),
        ],
    )

    report = await sync_group_administrators(group_id, apply=True)

    assert report.telegram == 1, "only one human admin was listed"
    assert set(await _roster(clean_db, group_id)) == {9601}
    assert await _credits(clean_db, 9602) is None
    assert await _credits(clean_db, GROUP_ANONYMOUS_BOT_ID) is None


@pytest.mark.asyncio
async def test_duplicate_ids_are_counted_once(patched_db_conn, clean_db, monkeypatch):
    group_id = -1001300008
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)

    _patch_admins(monkeypatch, [_admin(9701), _admin(9701)])

    report = await sync_group_administrators(group_id, apply=True)
    assert report.telegram == 1
    assert report.new == 1


# --- the empty-list trap -----------------------------------------------------


@pytest.mark.asyncio
async def test_empty_admin_list_demotes_nobody(patched_db_conn, clean_db, monkeypatch):
    """An empty read is a failed call, not a group with no admins."""
    group_id = -1001300009
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)
        await _seed_seat(conn, group_id, 9801, registered=True)

    _patch_admins(monkeypatch, [])

    report = await sync_group_administrators(group_id, apply=True)

    assert report.telegram == 0
    assert report.demoted == 0
    assert (await _roster(clean_db, group_id))[9801] == (True, True), (
        "one failed getChatAdministrators must never blank a live group's skip set"
    )


@pytest.mark.asyncio
async def test_empty_admin_list_preview_promises_no_demotion(
    patched_db_conn, clean_db, monkeypatch
):
    """The DRY RUN must not promise a write `--apply` refuses.

    Measured on the live fleet 2026-11-09: three groups reported `demoted=1`
    in the preview and `demoted=0` under `--apply`, because the empty-list
    guard zeroed the count on the write path only. An operator reading that
    preview would have expected three seats demoted that were never touched —
    and the preview is the only thing they have to decide on.
    """
    group_id = -1001300011
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)
        await _seed_seat(conn, group_id, 9802, registered=True)

    _patch_admins(monkeypatch, [])

    preview = await sync_group_administrators(group_id, apply=False)
    written = await sync_group_administrators(group_id, apply=True)

    assert preview.demoted == 0, "the preview must not promise a demotion it refuses"
    assert preview.demoted == written.demoted, (
        "preview and write must agree by construction, not only on the happy path"
    )
    assert (await _roster(clean_db, group_id))[9802] == (True, True)

@pytest.mark.asyncio
async def test_bot_only_admin_list_demotes_nobody(patched_db_conn, clean_db, monkeypatch):
    """The same trap one step in: a list of only bots is still not a real read."""
    group_id = -1001300010
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id)
        await _seed_seat(conn, group_id, 9901, registered=True)

    _patch_admins(monkeypatch, [_admin(9902, is_bot=True)])

    report = await sync_group_administrators(group_id, apply=True)

    assert report.telegram == 0
    assert report.demoted == 0
    assert (await _roster(clean_db, group_id))[9901] == (True, True)


@pytest.mark.asyncio
async def test_sync_reads_the_group_title(patched_db_conn, clean_db, monkeypatch):
    group_id = -1001300011
    async with clean_db.acquire() as conn:
        await _seed_group(conn, group_id, title="Тишина: сообщество инвесторов")

    _patch_admins(monkeypatch, [_admin(9911)])

    report = await sync_group_administrators(group_id, apply=False)
    assert report.title == "Тишина: сообщество инвесторов"


# --- the CLI runs from both layouts ------------------------------------------


def _run_script(args: list[str], *, cwd: str, env_extra: dict | None = None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "BOT_TOKEN": "123456:test-token-not-used",
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_cli_help_from_repo_root():
    """`python3 scripts/sync_group_admins.py` — the bootstrap finds src/app."""
    result = _run_script(
        ["scripts/sync_group_admins.py", "--help"], cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr
    assert "--apply" in result.stdout


def test_cli_help_via_pythonpath_only(tmp_path):
    """The image layout: the script sits elsewhere and `app` comes from PYTHONPATH.

    Copied out of the repo, so neither bootstrap candidate exists beside it —
    the import can only succeed through the PYTHONPATH=/app route the Docker
    image provides. Without this, a script that works from the repo root and
    dies in the container is discovered by a cron, at night.
    """
    shutil.copy(os.path.join(REPO_ROOT, "scripts", "sync_group_admins.py"), tmp_path)
    os.symlink(os.path.join(REPO_ROOT, "src", "app"), tmp_path / "app")

    result = _run_script(
        ["sync_group_admins.py", "--help"],
        cwd=str(tmp_path),
        env_extra={"PYTHONPATH": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert "--apply" in result.stdout


def test_cli_dry_run_is_the_default():
    """`--apply` is opt-in: a bare run must not be able to write."""
    result = _run_script(
        ["scripts/sync_group_admins.py", "--help"], cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr
    # argparse wraps the help text, so compare on collapsed whitespace.
    collapsed = " ".join(result.stdout.split())
    assert "--apply write the reconciled roster (default is a read-only dry run)" in (
        collapsed
    ), collapsed
