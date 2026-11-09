"""`scripts/recover_failed.credits_available` — the dry run's twin of the deduction.

The function documents itself as a read-only twin of `deduct_credits_from_admins`
("same join, same ordering, no UPDATE") and carried NEITHER of the two flags the
production query filters on. Measured 2026-11-09: a stranger's balance read as
payable, so the dry run's delete list over-stated what `--apply` would do — and
the twin's own docstring was the thing that made it look verified.
"""

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "recover_failed.py"


def _load_script():
    """Import `scripts/recover_failed.py` by path — `scripts/` is not a package."""
    spec = importlib.util.spec_from_file_location("recover_failed_under_test", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _seed_seat(conn, group_id, admin_id, *, credits, registered, admin_now):
    await conn.execute(
        "INSERT INTO groups (group_id) VALUES ($1) ON CONFLICT DO NOTHING", group_id
    )
    await conn.execute(
        "INSERT INTO administrators (admin_id, credits) VALUES ($1, $2) "
        "ON CONFLICT DO NOTHING",
        admin_id,
        credits,
    )
    await conn.execute(
        "INSERT INTO group_administrators "
        "(group_id, admin_id, is_registered, is_admin_now) VALUES ($1, $2, $3, $4)",
        group_id,
        admin_id,
        1 if registered else 0,
        1 if admin_now else 0,
    )


@pytest.mark.asyncio
async def test_credits_available_ignores_a_departed_admins_balance(
    patched_db_conn, clean_db
):
    """500 credits, but the holder left: the group cannot pay.

    Before the fix this returned True — the twin had no filter at all, so the
    highest balance in the table decided it whoever held it.
    """
    module = _load_script()
    group_id, departed = 882001, 882011

    async with clean_db.acquire() as conn:
        await _seed_seat(
            conn, group_id, departed, credits=500, registered=True, admin_now=False
        )

    assert await module.credits_available(group_id) is False


@pytest.mark.asyncio
async def test_credits_available_reads_a_sitting_admin(patched_db_conn, clean_db):
    """The positive control: the same balance on a sitting admin IS payable.

    Without this arm the test above would pass on a function that always
    returned False, which is the classic absence-sweep without a control.
    """
    module = _load_script()
    group_id, current = 882002, 882021

    async with clean_db.acquire() as conn:
        await _seed_seat(
            conn, group_id, current, credits=500, registered=True, admin_now=True
        )

    assert await module.credits_available(group_id) is True


@pytest.mark.asyncio
async def test_credits_available_ignores_a_roster_only_stranger(
    patched_db_conn, clean_db
):
    """Never opened the bot -> never a payer, however large the balance."""
    module = _load_script()
    group_id, stranger = 882003, 882031

    async with clean_db.acquire() as conn:
        await _seed_seat(
            conn, group_id, stranger, credits=500, registered=False, admin_now=True
        )

    assert await module.credits_available(group_id) is False
