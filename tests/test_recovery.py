"""Guard tests for the failed-classification recovery (scripts/recover_failed.py).

The recovery re-drives the classifications that were lost to the old per-attempt
timeout wall. Its whole safety story is one policy function plus the store's own
exactly-once guard, so these tests pin BOTH:

- the policy (``plan_target``) never replays a row that is gone, unknown, or
  already moderated — a recovery that re-moderated would delete a second time;
- the apply table never renders an approval as a deletion;
- the attempt reset puts a capped row back INSIDE the existing retry predicate
  rather than bypassing it, so the cap still bounds what follows;
- the audit is one row per message, however often it is rewritten.

The already-moderated branch is the guard whose failure is silent (a second
moderation of a live message), so it is the one the neuter check targets.
"""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "recover_failed.py"
_spec = importlib.util.spec_from_file_location("recover_failed", _SCRIPT)
rf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rf)


def _prior(status="failed", attempts=1, moderated_at=None):
    return {
        "status": status,
        "attempts": attempts,
        "moderated_at": moderated_at,
        "created_at": datetime.now(UTC),
    }


def _rec(chat_id, message_id, outcome, **kw):
    base = {
        "chat_id": chat_id,
        "message_id": message_id,
        "outcome": outcome,
        "prior_created_at": None,
    }
    base.update(kw)
    return base


# --- the policy: what may be replayed --------------------------------------


def test_recovery_present_failed_row_replays():
    """A live message with no verdict left to serve is replayed."""
    assert rf.plan_target(True, _prior()) == ("replay", "")


def test_recovery_gone_message_is_terminal_not_retried():
    """Already deleted from the group: there is nothing left to moderate."""
    assert rf.plan_target(False, _prior()) == ("terminal", "terminal_gone")


def test_recovery_unknown_presence_is_terminal():
    """Presence unreadable: 'unknown' must never become 'gone' OR 'replay'."""
    assert rf.plan_target(None, _prior()) == ("terminal", "terminal_unresolvable")


def test_recovery_already_moderated_is_never_moderated_again():
    """THE guard: moderated_at set must be terminal, never replayed.

    Neutering this branch makes plan_target fall through to the presence test and
    return ("replay", "") for a present message — moderating a message that was
    already moderated, i.e. deleting it a second time.
    """
    prior = _prior(moderated_at=datetime.now(UTC))
    assert rf.plan_target(True, prior) == ("terminal", "terminal_already_moderated")


# --- the apply table: an approval is not a deletion ------------------------


def test_recovery_apply_table_splits_recovered_by_is_spam():
    """In apply mode `recovered` means deleted OR approved — never assume deleted."""
    records = [
        _rec(-100, 1, "recovered", is_spam=True, confidence=100),
        _rec(-100, 2, "recovered", is_spam=False, confidence=100),
    ]
    table = rf.render_table(records, "t", apply=True)
    total = next(line for line in table.splitlines() if line.startswith("TOTAL"))
    # columns: delete / review / keep / term / exp<48h
    assert total.split() == ["TOTAL", "1", "0", "1", "0", "0"]

    # the approval must NOT appear under the deleted heading
    deleted_block = table.split("deleted (apply) for -100", 1)[1]
    assert "1(c100)" in deleted_block
    assert "2(" not in deleted_block


# --- the attempt reset stays inside the cap --------------------------------


@pytest.mark.asyncio
async def test_recovery_reset_attempts_returns_the_row_to_the_cap(
    patched_db_conn, clean_db
):
    """A capped row is put back INSIDE the predicate, and the cap still bounds it."""
    from app.database import DEFAULT_VERDICT_RETRY_ATTEMPTS as CAP
    from app.database.classification_verdicts import (
        claim_or_read,
        claim_pending,
        mark_failed,
        reclaim_failed,
    )

    await claim_pending(-100, 7)
    for _ in range(CAP):
        await mark_failed(-100, 7)
    assert (await claim_or_read(-100, 7))["attempts"] == CAP

    # at the cap the retry is refused — that is what ends it instead of looping
    assert await reclaim_failed(-100, 7, cooldown=timedelta(hours=-1)) is False

    await rf.reset_attempts(-100, 7)
    assert (await claim_or_read(-100, 7))["attempts"] == 0

    # the SAME predicate accepts it again — the row was moved, not the cap raised
    assert await reclaim_failed(-100, 7, cooldown=timedelta(hours=-1)) is True

    # and it is still bounded: CAP more failures caps it again
    for _ in range(CAP):
        await mark_failed(-100, 7)
    assert (await claim_or_read(-100, 7))["attempts"] == CAP
    assert await reclaim_failed(-100, 7, cooldown=timedelta(hours=-1)) is False


# --- the audit: one row per message ----------------------------------------


@pytest.mark.asyncio
async def test_recovery_audit_is_one_row_per_message(patched_db_conn, clean_db):
    """Re-auditing the same (run, chat, message) updates the row, never duplicates."""
    from app.database.postgres_connection import get_pool

    await rf.ensure_recovery_table()
    rec = {
        "chat_id": -100,
        "message_id": 9,
        "outcome": "terminal_gone",
        "presence": "gone",
        "detail": "terminal_gone",
    }
    await rf.write_audit("run-x", rec)
    await rf.write_audit(
        "run-x", dict(rec, outcome="recovered", is_spam=True, confidence=99)
    )

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT outcome, is_spam, confidence FROM recovery_attempts "
            "WHERE run_id='run-x' AND chat_id=-100 AND message_id=9"
        )
    assert len(rows) == 1
    assert rows[0]["outcome"] == "recovered"
    assert rows[0]["confidence"] == 99
