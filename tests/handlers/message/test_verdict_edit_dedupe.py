"""The edit-delivery dimension of the verdict gate (#142).

A redelivered `edited_message` used to re-classify and re-charge, because only
`source == "new"` was verdict-gated and an edit had no dedupe key. The store now
keys a delivery on `(chat_id, message_id, edit_date)`, so:

(a) a redelivery of the SAME edit classifies once, moderates once — the measured
    defect charged admin 500761740 twice, 42 s apart, with two notifications;
(b) a genuinely NEWER edit is a new unit of work and IS charged;
(c) a stale edit arriving after a newer one is a superseded delivery, not a
    duplicate: it must never roll the row back and never re-charge.

Moderation is where the credit deduction and the admin notification happen, so
`moderate_calls` is the count of both.
"""

import asyncio

import pytest

from app.database.classification_verdicts import claim_or_read, claim_pending
from app.handlers.message.verdict import (
    RESULT_VERDICT_REPLAYED,
    reset_verdict_state,
    run_with_verdict,
)

CHAT_ID = -100888
MESSAGE_ID = 555
ORIGINAL = 0
EDIT_A = 1_760_000_000
EDIT_B = 1_760_000_600


@pytest.fixture(autouse=True)
def _reset_gate_state():
    """Module-level state is process-wide; clear it between tests."""
    reset_verdict_state()
    yield
    reset_verdict_state()


async def _drain_inflight() -> None:
    """Wait for every detached task the gate started."""
    from app.handlers.message.verdict import inflight_tasks

    for _ in range(50):
        pending = inflight_tasks()
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
    raise AssertionError("detached verdict tasks did not finish")


class _Counter:
    """A classify/moderate pair that records how often each was called."""

    def __init__(self, verdict=(True, 97, "crypto_investment_scam")):
        self.verdict = verdict
        self.classify_calls = 0
        self.moderate_calls = 0

    async def classify(self):
        self.classify_calls += 1
        return self.verdict

    async def moderate(self, is_spam, confidence, reason):
        self.moderate_calls += 1
        return f"moderated:{is_spam}:{confidence}"


@pytest.mark.asyncio
async def test_redelivered_edit_charges_and_notifies_once(patched_db_conn, clean_db):
    """(a) The measured defect: the same edit delivered twice must bill once."""
    first = _Counter()
    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, first.classify, first.moderate, EDIT_A
    )
    await _drain_inflight()
    assert result == "moderated:True:97"
    assert first.classify_calls == 1
    assert first.moderate_calls == 1

    # Telegram redelivers the same edited update.
    second = _Counter()
    replay = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, second.classify, second.moderate, EDIT_A
    )
    await _drain_inflight()

    assert replay == RESULT_VERDICT_REPLAYED
    assert second.classify_calls == 0, "a redelivered edit must not be re-classified"
    assert second.moderate_calls == 0, (
        "exactly one deduction and one notification per (chat_id, message_id, "
        "edit_date)"
    )


@pytest.mark.asyncio
async def test_a_newer_edit_is_a_new_charged_delivery(patched_db_conn, clean_db):
    """(b) Positive control: changed content IS a new unit of work."""
    first = _Counter()
    await run_with_verdict(CHAT_ID, MESSAGE_ID, first.classify, first.moderate, EDIT_A)
    await _drain_inflight()

    second = _Counter()
    await run_with_verdict(CHAT_ID, MESSAGE_ID, second.classify, second.moderate, EDIT_B)
    await _drain_inflight()

    assert second.classify_calls == 1, "a new edit has new content to classify"
    assert second.moderate_calls == 1

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row is not None
    assert int(row["edit_date"]) == EDIT_B


@pytest.mark.asyncio
async def test_stale_edit_after_a_newer_one_never_recharges(patched_db_conn, clean_db):
    """(c) Out-of-order delivery is superseded, not duplicated."""
    newer = _Counter()
    await run_with_verdict(
        CHAT_ID, MESSAGE_ID, newer.classify, newer.moderate, EDIT_B
    )
    await _drain_inflight()
    assert newer.moderate_calls == 1

    # The older edit arrives late — Telegram does not guarantee delivery order.
    stale = _Counter()
    replay = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, stale.classify, stale.moderate, EDIT_A
    )
    await _drain_inflight()

    assert replay == RESULT_VERDICT_REPLAYED
    assert stale.classify_calls == 0
    assert stale.moderate_calls == 0, "a superseded delivery must never be billed"

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row is not None
    assert int(row["edit_date"]) == EDIT_B, "a stale edit must not roll the row back"


@pytest.mark.asyncio
async def test_original_then_edit_are_two_deliveries(patched_db_conn, clean_db):
    """The original message and its first edit are separate deliveries."""
    original = _Counter()
    await run_with_verdict(
        CHAT_ID, MESSAGE_ID, original.classify, original.moderate, ORIGINAL
    )
    await _drain_inflight()
    assert original.moderate_calls == 1

    edited = _Counter()
    await run_with_verdict(CHAT_ID, MESSAGE_ID, edited.classify, edited.moderate, EDIT_A)
    await _drain_inflight()

    assert edited.classify_calls == 1
    assert edited.moderate_calls == 1


@pytest.mark.asyncio
async def test_concurrent_deliveries_of_one_edit_bill_once(patched_db_conn, clean_db):
    """Two simultaneous copies of the same edit: one claim, one charge."""
    a = _Counter()
    b = _Counter()
    results = await asyncio.gather(
        run_with_verdict(CHAT_ID, MESSAGE_ID, a.classify, a.moderate, EDIT_A),
        run_with_verdict(CHAT_ID, MESSAGE_ID, b.classify, b.moderate, EDIT_A),
    )
    await _drain_inflight()

    assert a.moderate_calls + b.moderate_calls == 1
    assert a.classify_calls + b.classify_calls <= 1
    assert any(r == "moderated:True:97" for r in results)


@pytest.mark.asyncio
async def test_claim_pending_adopts_a_newer_edit(patched_db_conn, clean_db):
    """The store side of (b): the row moves forward, in place."""
    assert await claim_pending(CHAT_ID, MESSAGE_ID, ORIGINAL) is True
    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row is not None
    assert int(row["edit_date"]) == ORIGINAL

    assert await claim_pending(CHAT_ID, MESSAGE_ID, EDIT_A) is True
    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row is not None
    assert int(row["edit_date"]) == EDIT_A
    assert row["status"] == "pending", "an adopted edit is pending work again"


@pytest.mark.asyncio
async def test_claim_pending_refuses_a_stale_edit(patched_db_conn, clean_db):
    """Monotonic: a late older edit can never take the row backwards."""
    assert await claim_pending(CHAT_ID, MESSAGE_ID, EDIT_B) is True

    assert await claim_pending(CHAT_ID, MESSAGE_ID, EDIT_A) is False

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row is not None
    assert int(row["edit_date"]) == EDIT_B
