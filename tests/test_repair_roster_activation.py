"""#130 — the one-shot repair, and the guards that refuse it.

`ADMINISTRATORS_ACTIVATED_AT_BACKFILL` used to run on every boot. Run after the
roster prefill it stamped strangers as activated, and `initialize_new_admin`
keys the welcome and the 100 credits on that column — so 55 administrators
across 56 seats were denied both, permanently. Measured in production
2026-11-09 07:08:22–24Z.

Two cohorts, one transaction, **reset first**. Promote before reset would take
62 seats instead of 6, because at that moment the boot-stamped strangers still
carry `activated_at` — turning 56 people who never opened the bot into billing
contacts.

What these tests pin: a dry run writes NOTHING; the dry run's numbers are the
apply's numbers; a second apply reads 0/0; and each of the guards REFUSES with
nothing written. The guards are positive evidence — a transaction or a
registered seat means the predicate is reaching rows it must not touch — so
each one has its own arm rather than a single "guards work" test.
"""

import pytest

from app.database.group_operations import (
    collect_roster_repair,
    repair_roster_activation,
    roster_repair_guard_failures,
)

GROUP_A = -1001300101
GROUP_B = -1001300102

async def _seed(
    conn,
    admin_id: int,
    *,
    activated: bool = False,
    credits: int = 0,
    initial_grant: bool = False,
    other_transaction: bool = False,
) -> None:
    """One administrator row, shaped to land in one cohort or the other."""
    await conn.execute(
        "INSERT INTO administrators (admin_id, credits, activated_at) "
        "VALUES ($1, $2, CASE WHEN $3 THEN NOW() ELSE NULL END)",
        admin_id,
        credits,
        activated,
    )
    if initial_grant:
        await conn.execute(
            "INSERT INTO transactions (admin_id, amount, type) "
            "VALUES ($1, 100, 'initial')",
            admin_id,
        )
    if other_transaction:
        await conn.execute(
            "INSERT INTO transactions (admin_id, amount, type) "
            "VALUES ($1, -1, 'deduction')",
            admin_id,
        )

async def _seat(conn, group_id: int, admin_id: int, *, registered: bool, is_admin_now: bool = True) -> None:
    await conn.execute(
        "INSERT INTO groups (group_id) VALUES ($1) ON CONFLICT DO NOTHING", group_id
    )
    await conn.execute(
        "INSERT INTO group_administrators "
        "(group_id, admin_id, is_registered, is_admin_now) VALUES ($1, $2, $3, $4)",
        group_id,
        admin_id,
        registered,
        is_admin_now,
    )

async def _state(clean_db, admin_id: int) -> tuple[bool, bool]:
    async with clean_db.acquire() as conn:
        activated = await conn.fetchval(
            "SELECT activated_at FROM administrators WHERE admin_id = $1", admin_id
        )
        registered = await conn.fetchval(
            "SELECT COUNT(*) FROM group_administrators "
            "WHERE admin_id = $1 AND is_registered",
            admin_id,
        )
    return activated is not None, bool(registered)

# --- the two cohorts ---------------------------------------------------------

@pytest.mark.asyncio
async def test_reset_cohort_is_a_boot_stamp_and_never_a_real_activation(
    patched_db_conn, clean_db
):
    """Three rows, three verdicts — the discriminator is positive evidence.

    `activated_at = created_at` proves nothing (`NOW()` is transaction-constant,
    so every production row reads that way); a real activation leaves an
    `initial` transaction, and the legacy bot-add branch paid without one, so a
    balance is the second, independent proof.
    """
    boot_stamped, real_grant, legacy_paid = 9001, 9002, 9003
    async with clean_db.acquire() as conn:
        await _seed(conn, boot_stamped, activated=True, credits=0)
        await _seed(conn, real_grant, activated=True, credits=0, initial_grant=True)
        await _seed(conn, legacy_paid, activated=True, credits=55)

        report = await collect_roster_repair(conn)

    assert report["reset_admin_ids"] == [boot_stamped], report["reset_admin_ids"]
    assert report["reset_with_any_transaction"] == 0
    assert report["reset_with_registered_seat"] == 0

@pytest.mark.asyncio
async def test_promote_cohort_excludes_the_reset_cohort(patched_db_conn, clean_db):
    """The number a dry run prints is the number `--apply` writes.

    Without the exclusion the promote read counts the boot-stamped rows too —
    they still carry `activated_at` at read time — and promises 2 seats where
    the apply writes 1.
    """
    boot_stamped, proven = 9011, 9012
    async with clean_db.acquire() as conn:
        await _seed(conn, boot_stamped, activated=True)
        await _seed(conn, proven, activated=True, credits=0, initial_grant=True)
        await _seat(conn, GROUP_A, boot_stamped, registered=False)
        await _seat(conn, GROUP_A, proven, registered=False)

        report = await collect_roster_repair(conn)

    assert report["reset_admins"] == 1
    assert report["promote_seats"] == 1
    assert report["promote_admins"] == 1
    assert [row["admin_id"] for row in report["promote_rows"]] == [proven]
    assert report["promote_without_reset"] == 2, (
        "the pre-reset count must be printed beside the real one"
    )

@pytest.mark.asyncio
async def test_promote_cohort_requires_a_current_seat(patched_db_conn, clean_db):
    """A proven holder whose seat Telegram no longer lists is not promoted."""
    proven = 9021
    async with clean_db.acquire() as conn:
        await _seed(conn, proven, activated=True, credits=0, initial_grant=True)
        await _seat(conn, GROUP_A, proven, registered=False, is_admin_now=False)

        report = await collect_roster_repair(conn)

    assert report["promote_seats"] == 0

@pytest.mark.asyncio
async def test_promote_cohort_requires_proof_of_reachability(patched_db_conn, clean_db):
    """A roster-only seat whose holder never activated stays roster-only."""
    stranger = 9031
    async with clean_db.acquire() as conn:
        await _seed(conn, stranger)
        await _seat(conn, GROUP_A, stranger, registered=False)

        report = await collect_roster_repair(conn)

    assert report["promote_seats"] == 0

# --- the dry run -------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_writes_nothing(patched_db_conn, clean_db):
    boot_stamped, proven = 9041, 9042
    async with clean_db.acquire() as conn:
        await _seed(conn, boot_stamped, activated=True)
        await _seed(conn, proven, activated=True, credits=0, initial_grant=True)
        await _seat(conn, GROUP_A, boot_stamped, registered=False)
        await _seat(conn, GROUP_A, proven, registered=False)

        report = await repair_roster_activation(conn, apply=False)

    assert report["applied"] is False
    assert report["promote_written"] == 0
    assert (report["reset_admins"], report["promote_seats"]) == (1, 1)

    # Nothing moved: the boot stamp survives, both seats are still roster-only.
    assert await _state(clean_db, boot_stamped) == (True, False)
    assert await _state(clean_db, proven) == (True, False)

# --- apply -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_resets_then_promotes(patched_db_conn, clean_db):
    """The whole repair in one pass, and the order is visible in the result.

    Reset first: the boot-stamped row loses its `activated_at`, so the promote
    pass cannot see it. Promote after: the proven holder's seat becomes a
    billing row.
    """
    boot_stamped, proven = 9051, 9052
    async with clean_db.acquire() as conn:
        await _seed(conn, boot_stamped, activated=True)
        await _seed(conn, proven, activated=True, credits=0, initial_grant=True)
        await _seat(conn, GROUP_A, boot_stamped, registered=False)
        await _seat(conn, GROUP_A, proven, registered=False)

        report = await repair_roster_activation(conn, apply=True)

    assert report["applied"] is True
    assert report["promote_written"] == 1
    assert report["after"] == {"reset_admins": 0, "reset_seats": 0, "promote_seats": 0}

    # The stranger is un-stamped and still roster-only — he keeps his welcome.
    assert await _state(clean_db, boot_stamped) == (False, False)
    # The proven holder's seat is now a billing row.
    assert await _state(clean_db, proven) == (True, True)

@pytest.mark.asyncio
async def test_a_second_apply_reads_zero_and_writes_zero(patched_db_conn, clean_db):
    """Idempotent by state, not by a flag: the read after the write says so."""
    boot_stamped, proven = 9061, 9062
    async with clean_db.acquire() as conn:
        await _seed(conn, boot_stamped, activated=True)
        await _seed(conn, proven, activated=True, credits=0, initial_grant=True)
        await _seat(conn, GROUP_A, boot_stamped, registered=False)
        await _seat(conn, GROUP_A, proven, registered=False)

        first = await repair_roster_activation(conn, apply=True)
        second = await repair_roster_activation(conn, apply=True)

    assert (first["reset_admins"], first["promote_seats"]) == (1, 1)
    assert (second["reset_admins"], second["promote_seats"]) == (0, 0)
    assert second["promote_written"] == 0

@pytest.mark.asyncio
async def test_group_scope_limits_both_cohorts(patched_db_conn, clean_db):
    """`-g` is a scope, not a filter on the printed list."""
    in_a, in_b = 9071, 9072
    async with clean_db.acquire() as conn:
        await _seed(conn, in_a, activated=True)
        await _seed(conn, in_b, activated=True)
        await _seat(conn, GROUP_A, in_a, registered=False)
        await _seat(conn, GROUP_B, in_b, registered=False)

        report = await repair_roster_activation(conn, apply=True, group_ids=[GROUP_A])

    assert report["reset_admin_ids"] == [in_a]
    assert await _state(clean_db, in_a) == (False, False)
    # The other group is untouched — its boot stamp is still there.
    assert await _state(clean_db, in_b) == (True, False)

# --- the guards --------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_reset_candidate_carrying_a_transaction_refuses(patched_db_conn, clean_db):
    """A row with ANY transaction has billing history; the predicate is wrong."""
    admin_id = 9081
    async with clean_db.acquire() as conn:
        await _seed(conn, admin_id, activated=True, other_transaction=True)
        await _seat(conn, GROUP_A, admin_id, registered=False)

        report = await repair_roster_activation(conn, apply=True)

    assert report.get("refused"), "a tripped guard must refuse"
    assert report["applied"] is False
    assert await _state(clean_db, admin_id) == (True, False), "the refusal wrote"

@pytest.mark.asyncio
async def test_a_reset_candidate_holding_a_registered_seat_refuses(
    patched_db_conn, clean_db
):
    """A registered seat means somebody's current payer — never reset them."""
    admin_id = 9091
    async with clean_db.acquire() as conn:
        await _seed(conn, admin_id, activated=True)
        await _seat(conn, GROUP_A, admin_id, registered=True)

        report = await repair_roster_activation(conn, apply=True)

    assert report.get("refused")
    assert report["applied"] is False
    assert await _state(clean_db, admin_id) == (True, True)

@pytest.mark.asyncio
async def test_the_dry_run_refuses_on_the_same_guards(patched_db_conn, clean_db):
    """A preview that would be refused must say so, not print a plan."""
    admin_id = 9101
    async with clean_db.acquire() as conn:
        await _seed(conn, admin_id, activated=True, other_transaction=True)
        await _seat(conn, GROUP_A, admin_id, registered=False)

        report = await repair_roster_activation(conn, apply=False)

    assert report.get("refused"), "the dry run promised a write the apply refuses"

def test_the_guard_predicate_names_each_failure():
    """Both guards are independent; a report with neither reads clean."""
    clean = {
        "reset_with_any_transaction": 0,
        "reset_with_registered_seat": 0,
    }
    assert roster_repair_guard_failures(clean) == []
    assert len(
        roster_repair_guard_failures(
            {**clean, "reset_with_any_transaction": 3}
        )
    ) == 1
    assert len(
        roster_repair_guard_failures(
            {**clean, "reset_with_registered_seat": 2}
        )
    ) == 1
    assert len(
        roster_repair_guard_failures(
            {**clean, "reset_with_any_transaction": 3, "reset_with_registered_seat": 2}
        )
    ) == 2
