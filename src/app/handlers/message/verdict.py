"""Verdict-gated moderation for the new-message path.

The webhook guard cancels work at the deadline, so the classification is
detached and its verdict persisted (see `database.classification_verdicts`).
This module is the gate in front of that work: a redelivery of the same update
SERVES the stored verdict instead of paying for a second LLM call, and
moderation is claimed exactly once however many deliveries arrive.

States a delivery can meet, and what each does:

    absent   -> claim the row, detach the work, return the verdict if it lands
                inside the budget, else PENDING (the caller answers 503 + retry)
    pending  -> another worker owns it; return PENDING immediately, no LLM call
    decided  -> moderate from the stored verdict, no LLM call; the moderation
                claim makes N deliveries produce exactly one action
    failed   -> every leg was exhausted; return FAILED, no LLM call

An unavailable store is NOT the absent case. `STORE_UNAVAILABLE` is a distinct
sentinel and the caller short-circuits to the inline, un-gated path - today's
behaviour - so a store failure degrades the fix rather than breaking moderation.
The degradation is logged as an ERROR, once per process, so it is visible
instead of silent.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from ...common.llm_budget import get_llm_budget_seconds
from ...common.trace_context import remaining_webhook_seconds
from ...database import (
    claim_moderation,
    claim_or_read,
    claim_pending,
    mark_failed,
    release_moderation_claim,
    store_result_id,
    store_verdict,
)

logger = logging.getLogger(__name__)

RESULT_VERDICT_PENDING = "message_classification_pending"
RESULT_VERDICT_REPLAYED = "message_verdict_replayed"
RESULT_VERDICT_FAILED = "message_classification_failed"

# Distinct from None ("no row"). A store that cannot be reached must never be
# read as an absent verdict, or every delivery would re-classify and the
# failure would be invisible.
STORE_UNAVAILABLE = object()

ClassifyFn = Callable[[], Awaitable[tuple[bool, int, str]]]
ModerateFn = Callable[[bool, int, str], Awaitable[str]]

_inflight: set[asyncio.Task] = set()
_store_error_logged = False


def _log_store_error(exc: Exception, operation: str) -> None:
    """Log a store failure once per process, then at debug level."""
    global _store_error_logged
    if not _store_error_logged:
        _store_error_logged = True
        logger.error(
            f"Verdict store unavailable during {operation}: {exc!r}. "
            "Falling back to the un-gated inline path until the store recovers."
        )
    else:
        logger.debug(f"Verdict store unavailable during {operation}: {exc!r}")


async def _store_call(operation: str, awaitable):
    """Run one store call, returning STORE_UNAVAILABLE instead of raising."""
    try:
        return await awaitable
    except Exception as e:  # noqa: BLE001 - any store failure degrades, never breaks
        _log_store_error(e, operation)
        return STORE_UNAVAILABLE


def _on_task_done(task: asyncio.Task) -> None:
    """Discard a finished detached task and surface an unretrieved exception."""
    _inflight.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(f"Detached verdict task failed: {exc!r}")


def inflight_tasks() -> set[asyncio.Task]:
    """The detached verdict tasks still running. main.py drains these at shutdown."""
    return set(_inflight)


def reset_verdict_state() -> None:
    """Clear module state (for tests)."""
    global _store_error_logged
    _store_error_logged = False
    _inflight.clear()


async def _inline(classify: ClassifyFn, moderate: ModerateFn) -> str:
    """The un-gated path: classify then moderate, in the caller's task."""
    is_spam, confidence, reason = await classify()
    return await moderate(is_spam, confidence, reason)


async def _moderate_and_record(
    chat_id: int,
    message_id: int,
    is_spam: bool,
    confidence: int,
    reason: str,
    moderate: ModerateFn,
) -> str:
    """Moderate from a known verdict, recording the result id.

    On failure the moderation claim is released so a later delivery retries the
    action instead of the message being stuck moderated-never.
    """
    try:
        result = await moderate(is_spam, confidence, reason)
    except Exception as e:  # noqa: BLE001 - retried by the next delivery
        logger.warning(
            f"Moderation failed for {chat_id}/{message_id}: {e!r}; "
            "releasing the claim so the next delivery retries"
        )
        await _store_call(
            "release_moderation_claim", release_moderation_claim(chat_id, message_id)
        )
        row = await _store_call("claim_or_read", claim_or_read(chat_id, message_id))
        if row is not STORE_UNAVAILABLE and row is not None and row.get("result_id"):
            return row["result_id"]
        return RESULT_VERDICT_FAILED

    await _store_call("store_result_id", store_result_id(chat_id, message_id, result))
    return result


async def _finish(
    chat_id: int,
    message_id: int,
    classify: ClassifyFn,
    moderate: ModerateFn,
) -> str:
    """Classify, persist the verdict, then moderate exactly once.

    Runs in its own task so the webhook guard cannot cancel it mid-flight.
    """
    try:
        is_spam, confidence, reason = await classify()
    except Exception as e:  # noqa: BLE001 - every leg was exhausted
        logger.warning(f"Classification failed for {chat_id}/{message_id}: {e!r}")
        await _store_call("mark_failed", mark_failed(chat_id, message_id))
        return RESULT_VERDICT_FAILED

    stored = await _store_call(
        "store_verdict",
        store_verdict(chat_id, message_id, is_spam, confidence, reason),
    )
    if stored is STORE_UNAVAILABLE:
        # The verdict cannot be recorded, so nothing can serve it later.
        # Moderate now rather than lose the decision entirely.
        return await _moderate_and_record(
            chat_id, message_id, is_spam, confidence, reason, moderate
        )

    won = await _store_call("claim_moderation", claim_moderation(chat_id, message_id))
    if won is False:
        # Another delivery already moderated this message.
        return RESULT_VERDICT_REPLAYED
    # True, or the store failed while claiming: moderate either way, because a
    # store failure must never leave a decided verdict unmoderated.
    return await _moderate_and_record(
        chat_id, message_id, is_spam, confidence, reason, moderate
    )


async def _moderate_decided(
    chat_id: int, message_id: int, row: dict, moderate: ModerateFn
) -> str:
    """Serve a stored verdict: moderate it, with no LLM call."""
    won = await _store_call("claim_moderation", claim_moderation(chat_id, message_id))
    if won is False:
        return RESULT_VERDICT_REPLAYED
    return await _moderate_and_record(
        chat_id,
        message_id,
        bool(row["is_spam"]),
        int(row["confidence"]),
        row["reason"] or "",
        moderate,
    )


async def run_with_verdict(
    chat_id: int,
    message_id: int,
    classify: ClassifyFn,
    moderate: ModerateFn,
) -> str:
    """Gate one message's classification and moderation on the verdict store.

    Returns the moderation result id, or one of the three RESULT_VERDICT_*
    constants. Never raises for a store failure: the inline path is taken
    instead, so moderation keeps working while the store is down.
    """
    row = await _store_call("claim_or_read", claim_or_read(chat_id, message_id))
    if row is STORE_UNAVAILABLE:
        return await _inline(classify, moderate)

    if row is not None:
        status = row["status"]
        if status == "pending":
            return RESULT_VERDICT_PENDING
        if status == "failed":
            return RESULT_VERDICT_FAILED
        if status == "decided":
            return await _moderate_decided(chat_id, message_id, row, moderate)

    won = await _store_call("claim_pending", claim_pending(chat_id, message_id))
    if won is STORE_UNAVAILABLE:
        return await _inline(classify, moderate)
    if won is False:
        # A concurrent delivery claimed it between the read and the insert.
        return RESULT_VERDICT_PENDING

    task = asyncio.create_task(_finish(chat_id, message_id, classify, moderate))
    _inflight.add(task)
    task.add_done_callback(_on_task_done)

    remaining = remaining_webhook_seconds()
    if remaining is None:
        # No request context (direct call, background job, test).
        remaining = get_llm_budget_seconds()
    if remaining <= 0:
        # Already out of time: the task runs on, the caller answers 503.
        return RESULT_VERDICT_PENDING

    try:
        async with asyncio.timeout(remaining):
            # shield, not wait_for: the timeout must cancel the WAIT, never the
            # work - the task keeps running and still persists its verdict.
            return await asyncio.shield(task)
    except TimeoutError:
        return RESULT_VERDICT_PENDING
