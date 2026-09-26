"""Tests for the verdict gate (src/app/handlers/message/verdict.py).

The gate exists so a redelivery of the same update serves the stored verdict
instead of paying for a second LLM call, and so moderation happens exactly once
however many deliveries arrive. These tests pin the four properties the design
names, plus the store-failure fallback:

(a) a decided row replays: no classification, and the SECOND delivery moderates
    nothing (the claim is taken);
(b) a pending row returns PENDING with classification never called;
(c) a classification that outlives the deadline returns PENDING while the
    detached task still finishes and leaves a decided row;
(d) two concurrent deliveries classify once and moderate once;
(e) an unavailable store falls back to the inline path instead of breaking.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.common.trace_context import set_webhook_deadline
from app.database.classification_verdicts import (
    DEFAULT_VERDICT_RETRY_ATTEMPTS,
    DEFAULT_VERDICT_RETRY_COOLDOWN_SECONDS,
    claim_or_read,
    claim_pending,
    store_verdict,
)
from app.handlers.message.verdict import (
    RESULT_VERDICT_FAILED,
    RESULT_VERDICT_PENDING,
    RESULT_VERDICT_REPLAYED,
    RESULT_VERDICT_RETRY_LATER,
    reset_verdict_state,
    run_with_verdict,
)

CHAT_ID = -100777
MESSAGE_ID = 31337


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

    def __init__(self, verdict=(True, 97, "crypto_investment_scam"), delay=0.0):
        self.verdict = verdict
        self.delay = delay
        self.classify_calls = 0
        self.moderate_calls = 0

    async def classify(self):
        self.classify_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.verdict

    async def moderate(self, is_spam, confidence, reason):
        self.moderate_calls += 1
        return f"moderated:{is_spam}:{confidence}"


@pytest.mark.asyncio
async def test_decided_row_replays_without_classifying(patched_db_conn, clean_db):
    """(a) A stored verdict is served; the second delivery moderates nothing."""
    assert await claim_pending(CHAT_ID, MESSAGE_ID) is True
    await store_verdict(CHAT_ID, MESSAGE_ID, True, 97, "crypto_investment_scam")

    first = _Counter()
    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, first.classify, first.moderate
    )
    assert result == "moderated:True:97"
    assert first.classify_calls == 0, "a stored verdict must not be re-classified"
    assert first.moderate_calls == 1

    # The same update arrives again: the moderation claim is already taken.
    second = _Counter()
    replay = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, second.classify, second.moderate
    )
    assert replay == RESULT_VERDICT_REPLAYED
    assert second.classify_calls == 0
    assert second.moderate_calls == 0, "one message must be moderated exactly once"


@pytest.mark.asyncio
async def test_pending_row_returns_pending_without_classifying(
    patched_db_conn, clean_db
):
    """(b) Another worker owns the message; this delivery does no work."""
    assert await claim_pending(CHAT_ID, MESSAGE_ID) is True

    counter = _Counter()
    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, counter.classify, counter.moderate
    )

    assert result == RESULT_VERDICT_PENDING
    assert counter.classify_calls == 0
    assert counter.moderate_calls == 0


@pytest.mark.asyncio
async def test_slow_classification_returns_pending_then_persists(
    patched_db_conn, clean_db
):
    """(c) The caller gives up at the deadline; the work still lands."""
    set_webhook_deadline(0.05)
    counter = _Counter(delay=0.3)

    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, counter.classify, counter.moderate
    )
    assert result == RESULT_VERDICT_PENDING
    assert counter.moderate_calls == 0, "the caller must not have moderated yet"

    # The detached task outlives the caller: the verdict still gets written.
    await _drain_inflight()

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row is not None
    assert row["status"] == "decided"
    assert row["confidence"] == 97
    assert counter.moderate_calls == 1, "the detached task moderates exactly once"


@pytest.mark.asyncio
async def test_concurrent_deliveries_classify_and_moderate_once(
    patched_db_conn, clean_db
):
    """(d) Two deliveries of one update: one classification, one moderation."""
    counter = _Counter()

    first, second = await asyncio.gather(
        run_with_verdict(CHAT_ID, MESSAGE_ID, counter.classify, counter.moderate),
        run_with_verdict(CHAT_ID, MESSAGE_ID, counter.classify, counter.moderate),
    )
    await _drain_inflight()

    assert counter.classify_calls == 1, "the claim must admit exactly one worker"
    assert counter.moderate_calls == 1, "moderation must happen exactly once"
    assert RESULT_VERDICT_PENDING in (first, second) or first == second, (
        "the losing delivery reports pending or a replay, never its own verdict"
    )

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["status"] == "decided"
    assert row["moderated_at"] is not None


@pytest.mark.asyncio
async def test_unavailable_store_falls_back_to_inline(patched_db_conn, clean_db):
    """(e) A store failure degrades the fix; it never breaks moderation."""
    from app.handlers.message import verdict as verdict_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("store is down")

    original = verdict_module.claim_or_read
    verdict_module.claim_or_read = _boom
    try:
        counter = _Counter()
        result = await run_with_verdict(
            CHAT_ID, MESSAGE_ID, counter.classify, counter.moderate
        )
    finally:
        verdict_module.claim_or_read = original

    assert result == "moderated:True:97", "the inline path must still moderate"
    assert counter.classify_calls == 1
    assert counter.moderate_calls == 1


@pytest.mark.asyncio
async def test_classification_failure_is_recorded_as_failed(
    patched_db_conn, clean_db
):
    """A classify that raises marks the row failed; the next delivery re-asks.

    It must NOT re-classify straight away: the cooldown is a backoff, so the
    delivery arriving immediately after the failure is told to come back rather
    than paying for the same failing call again.
    """

    async def _fail():
        raise RuntimeError("every leg exhausted")

    counter = _Counter()
    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, _fail, counter.moderate
    )
    assert result == RESULT_VERDICT_FAILED

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["status"] == "failed"
    assert row["attempts"] == 1

    # One second old, with the default 60s cooldown. Written explicitly because
    # a NOW()-written row cannot be compared against a bound datetime offline -
    # see _fail_at. The predicate under test is "a recent failure is not
    # reclaimable", and PostgreSQL reads the same instant without the shim.
    await _fail_at(clean_db, datetime.now(UTC) - timedelta(seconds=1))

    retry = _Counter()
    assert await run_with_verdict(
        CHAT_ID, MESSAGE_ID, retry.classify, retry.moderate
    ) == RESULT_VERDICT_RETRY_LATER
    assert retry.classify_calls == 0, "the cooldown must hold the retry back"

async def _fail_at(clean_db, when) -> None:
    """Set the failed row's `decided_at` to an explicit instant.

    Load-bearing, not tidiness: `mark_failed` writes `decided_at = NOW()`, which
    SQLite renders with a SPACE, while conftest binds a datetime parameter with a
    T - and space sorts below T, so a NOW()-written column compares as older
    than ANY bound instant. Writing both sides through the same adapter keeps
    the ordering real. PostgreSQL compares true timestamptz and is unaffected.
    """
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE classification_verdicts SET decided_at = $1 "
            "WHERE chat_id = $2 AND message_id = $3",
            when,
            CHAT_ID,
            MESSAGE_ID,
        )

@pytest.mark.asyncio
async def test_failed_row_is_reclaimed_past_the_cooldown(patched_db_conn, clean_db):
    """The retry itself: past the cooldown the work is run again."""

    async def _fail():
        raise RuntimeError("every leg exhausted")

    await run_with_verdict(CHAT_ID, MESSAGE_ID, _fail, _Counter().moderate)
    await _fail_at(clean_db, datetime.now(UTC) - timedelta(hours=1))

    counter = _Counter()
    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, counter.classify, counter.moderate
    )
    await _drain_inflight()

    assert counter.classify_calls == 1, "the reclaim must re-run the classification"
    assert counter.moderate_calls == 1
    assert result == "moderated:True:97"

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["status"] == "decided"
    assert row["moderated_at"] is not None

@pytest.mark.asyncio
async def test_failed_row_gives_up_at_the_attempt_cap(patched_db_conn, clean_db):
    """At the cap the retry stops, which is what ends it instead of looping."""
    from app.handlers.message.verdict import _retry_policy

    max_attempts, _cooldown = _retry_policy()

    async def _fail():
        raise RuntimeError("every leg exhausted")

    # Each round exhausts the legs again; the explicit timestamp makes the row
    # reclaimable every time, so this walks attempts up to the cap.
    for _ in range(max_attempts):
        await run_with_verdict(CHAT_ID, MESSAGE_ID, _fail, _Counter().moderate)
        await _fail_at(clean_db, datetime.now(UTC) - timedelta(hours=1))

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["attempts"] == max_attempts
    assert row["status"] == "failed"

    counter = _Counter()
    assert await run_with_verdict(
        CHAT_ID, MESSAGE_ID, counter.classify, counter.moderate
    ) == RESULT_VERDICT_FAILED
    assert counter.classify_calls == 0, "a capped row must not be re-classified"

@pytest.mark.asyncio
async def test_reclaim_is_not_attempted_for_a_decided_row(patched_db_conn, clean_db):
    """Only a FAILED row is reclaimable: a verdict is served, never re-asked."""
    assert await claim_pending(CHAT_ID, MESSAGE_ID) is True
    await store_verdict(CHAT_ID, MESSAGE_ID, False, 5, "ham")

    counter = _Counter()
    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, counter.classify, counter.moderate
    )

    assert counter.classify_calls == 0
    assert result == "moderated:False:5"
    assert (await claim_or_read(CHAT_ID, MESSAGE_ID))["status"] == "decided"

# ─── the retry policy is READ, and the shipped config carries it ─────────────

def test_retry_policy_reads_the_config_keys(monkeypatch):
    """The knobs are read, not decorative.

    A declared value that nothing consumes is the defect class this whole plan
    exists to fix, so the wiring is asserted rather than assumed.
    """
    from app.handlers.message import verdict as verdict_module

    monkeypatch.setattr(
        verdict_module,
        "load_config",
        lambda: {
            "cache": {
                "verdict_retry_attempts": 7,
                "verdict_retry_cooldown_seconds": 120,
            }
        },
    )

    attempts, cooldown = verdict_module._retry_policy()
    assert attempts == 7
    assert cooldown == timedelta(seconds=120)

def test_retry_policy_defaults_when_the_keys_are_absent(monkeypatch):
    """A missing key keeps the bounded behaviour instead of disabling it."""
    from app.handlers.message import verdict as verdict_module

    monkeypatch.setattr(verdict_module, "load_config", lambda: {"cache": {}})

    attempts, cooldown = verdict_module._retry_policy()
    assert attempts == DEFAULT_VERDICT_RETRY_ATTEMPTS
    assert cooldown == timedelta(seconds=DEFAULT_VERDICT_RETRY_COOLDOWN_SECONDS)

def test_shipped_config_carries_the_retry_keys():
    """config.yaml itself carries them, at an indentation that resolves.

    Read without monkeypatching, because the failure this catches is a config
    typo: a key nested one level too deep is silently absent, the code falls
    back to the default, and the knob looks configured while doing nothing.
    """
    from app.handlers.message import verdict as verdict_module

    attempts, cooldown = verdict_module._retry_policy()
    assert attempts == 3, "config.yaml cache.verdict_retry_attempts must resolve"
    assert cooldown == timedelta(seconds=60), (
        "config.yaml cache.verdict_retry_cooldown_seconds must resolve"
    )
