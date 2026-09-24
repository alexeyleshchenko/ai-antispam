"""Forced-slow behavioural verification for the classifier budget + verdict work.

Four scenarios, all deterministic and in-process. A slow model is SIMULATED by
a classify coroutine that sleeps; production is never mutated.

    1. fast path    - classify returns promptly: 200 inside the budget, the row
                      is decided, moderation happens once.
    2. slow path    - classify sleeps past the deadline: 503 + retry, and the
                      detached task STILL completes and leaves a decided row.
    3. one moderation - the same update replayed after the verdict exists: no
                      second classification, no second moderation.
    4. log line     - post-deploy only: no new "Webhook processing timed out
                      after" events. Baseline is measured from the live
                      container; see the note at the bottom of this file.

Scenarios 1-3 run here and now. Scenario 4 cannot run before the deploy it
measures, so its baseline is recorded and the post-deploy check is owed.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.classification_verdicts import claim_or_read, claim_pending
from app.handlers.message.verdict import (
    RESULT_VERDICT_PENDING,
    inflight_tasks,
    reset_verdict_state,
    run_with_verdict,
)

CHAT_ID = -100555
MESSAGE_ID = 8080


@pytest.fixture(autouse=True)
def _reset_gate():
    reset_verdict_state()
    yield
    reset_verdict_state()


class Scenario:
    """A classify/moderate pair with a controllable delay, plus call counts."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.classify_calls = 0
        self.moderate_calls = 0
        self.timings: list[float] = []

    async def classify(self):
        self.classify_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return True, 96, "crypto_investment_scam"

    async def moderate(self, is_spam, confidence, reason):
        self.moderate_calls += 1
        self.timings.append(time.monotonic())
        return f"spam_auto_deleted:{confidence}"


async def _drain() -> None:
    for _ in range(50):
        pending = inflight_tasks()
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)
    raise AssertionError("detached tasks did not finish")


@pytest.mark.asyncio
async def test_scenario_1_fast_path(patched_db_conn, clean_db):
    """Fast classify: the verdict is stored and moderation happens once."""
    scenario = Scenario()
    started = time.monotonic()

    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, scenario.classify, scenario.moderate
    )
    elapsed = time.monotonic() - started
    await _drain()

    assert result == "spam_auto_deleted:96"
    assert scenario.classify_calls == 1
    assert scenario.moderate_calls == 1

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["status"] == "decided"
    assert row["moderated_at"] is not None

    print(f"\n  scenario 1 (fast): result={result} elapsed={elapsed:.3f}s")


@pytest.mark.asyncio
async def test_scenario_2_slow_path(patched_db_conn, clean_db):
    """Slow classify: the caller gives up, the work survives and lands."""
    from app.common.trace_context import set_webhook_deadline

    set_webhook_deadline(0.1)
    scenario = Scenario(delay=0.5)
    started = time.monotonic()

    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, scenario.classify, scenario.moderate
    )
    elapsed = time.monotonic() - started

    assert result == RESULT_VERDICT_PENDING
    assert scenario.moderate_calls == 0, "the caller must not have moderated"
    assert elapsed < 0.5, "the caller must return BEFORE the classification ends"

    pending = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert pending["status"] == "pending", "the row is the in-flight marker"

    await _drain()

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["status"] == "decided", "the detached work must still persist"
    assert row["confidence"] == 96
    assert row["moderated_at"] is not None
    assert scenario.moderate_calls == 1

    print(
        f"\n  scenario 2 (slow): caller returned in {elapsed:.3f}s with PENDING; "
        f"verdict landed and moderated exactly once"
    )


@pytest.mark.asyncio
async def test_scenario_3_replay_moderates_once(patched_db_conn, clean_db):
    """A replay serves the verdict: no second classification, no second action."""
    first = Scenario()
    await run_with_verdict(CHAT_ID, MESSAGE_ID, first.classify, first.moderate)
    await _drain()
    assert first.classify_calls == 1
    assert first.moderate_calls == 1

    replay = Scenario()
    started = time.monotonic()
    result = await run_with_verdict(
        CHAT_ID, MESSAGE_ID, replay.classify, replay.moderate
    )
    elapsed = time.monotonic() - started

    assert replay.classify_calls == 0, "a replay must not re-classify"
    assert replay.moderate_calls == 0, "a replay must not moderate again"
    assert result == "message_verdict_replayed"

    row = await claim_or_read(CHAT_ID, MESSAGE_ID)
    assert row["status"] == "decided"

    print(
        f"\n  scenario 3 (replay): returned {result} in {elapsed:.4f}s with "
        f"0 classifications and 0 moderations"
    )


@pytest.mark.asyncio
async def test_scenario_4_log_line_post_deploy_is_owed():
    """Scenario 4 cannot run before the deploy it measures.

    It asserts that the REAL container log shows no new
    "Webhook processing timed out after" lines over a window of at least 72 h
    starting at the deploy. The baseline - 45 events in 120 h, bursts on four
    of five days - was measured from the live container BEFORE the fix.

    This test states the obligation rather than pretending to discharge it: a
    check that runs before its own subject exists would pass vacuously, which is
    the failure mode the verification law forbids.
    """
    pytest.skip(
        "post-deploy check: run `ssh apps 'docker logs --since <deploy> "
        "ai-antispam 2>&1 | grep -c \"Webhook processing timed out after\"'` "
        ">= 72 h after the deploy; expect 0 against the 45-event / 120 h baseline"
    )
