#!/usr/bin/env python3
"""Failed-classification recovery — replay the lost updates from Logfire.

Why this exists (2026-10-02)
----------------------------
176 messages sat in ``classification_verdicts`` as ``status='failed'``: the
classifier never reached a verdict, so moderation never ran — never deleted,
never approved, never reviewed. The timeout-ladder fix stops *new* losses; it
does not recover these. The owner's order:

    "if these failed messages are still there in the groups, we need to bring
     them back into the queue"

The verdict store keeps no message text, and ``message_lookup_cache`` is only
written AFTER a successful classification, so the store itself cannot replay
them. **Logfire can.** The webhook's root span carries the whole update
(``attributes->'update'``), and ``aiogram.types.Update.model_validate`` rebuilds
the real ``Message`` — chat, sender, id and text. Measured: 175 of 176 failed
rows have their update in Logfire, and the one exception is itself one of the
messages already gone from its group.

So recovery REPLAYS the original update through the production handler
(``handle_moderated_message(message, source="new")``). No new classification
path, no new moderation path, no re-implemented gate: with the row still
``failed``, the existing verdict gate does the rest (``_retry_failed`` ->
``reclaim_failed`` -> ``_detach`` -> classify -> ``store_verdict`` ->
``claim_moderation`` -> ``process_spam_or_approve``). Exactly-once comes from
the store's ``moderated_at IS NULL`` guard, not from new code here.

Dry-run is the default; ``--apply`` is required to touch Telegram
-----------------------------------------------------------------
Enqueueing is reversible; moderation is not (a spam verdict deletes the message
and deducts a star from the admin's balance). So the default pass classifies,
audits and prints — it writes nothing to Telegram and nothing to the verdict
store — and the delete list is reviewed before ``--apply``.

Two instrument traps this file exists to avoid (both cost a wrong number first)
-------------------------------------------------------------------------------
1. ``LogfireQueryClient.query_json_rows`` has a **default row limit of 100**. A
   coverage query that returns exactly the limit is TRUNCATED, not answered.
   ``limit`` is passed explicitly here, and a result that equals the limit is
   reported as possibly-truncated rather than read as the population.
2. A presence test built on a ``min_id``/``max_id`` window returns empty even
   for messages visible in a live page (it produced a false "173 of 176
   deleted"). The validated form is ``offset_id = message_id + 1, limit=1``:
   the bridge returns the highest id <= message_id, so the message is PRESENT
   iff that id equals ``message_id``.

Exit codes: 0 = completed, 1 = nothing to do, 2 = read error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

# `app` is importable in the image (PYTHONPATH=/app); from the repo it lives
# under src/. Add whichever parent holds it so the script runs in both places.
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (os.path.dirname(_HERE), os.path.join(os.path.dirname(_HERE), "src")):
    if os.path.isdir(os.path.join(_cand, "app")) and _cand not in sys.path:
        sys.path.insert(0, _cand)

logger = logging.getLogger("recover_failed")

FAILED_TAG = "message_classification_failed"
DEFAULT_LOOKBACK_DAYS = 30
WORKLIST_LIMIT = 5000
# The runner is not a webhook turn, so its "request deadline" is just a generous
# ceiling: it exists so `_detach` waits for the classification instead of
# answering PENDING, which keeps the batch sequential and the audit exact.
RECOVERY_TURN_SECONDS = 600.0
PRESENCE_TIMEOUT = 25

RECOVERY_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS recovery_attempts (
    id SERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT,
    presence TEXT,
    prior_status TEXT,
    prior_attempts INTEGER,
    prior_moderated_at TIMESTAMPTZ,
    prior_created_at TIMESTAMPTZ,
    is_spam BOOLEAN,
    confidence INTEGER,
    verdict_reason TEXT,
    result_id TEXT,
    moderated_at TIMESTAMPTZ,
    source TEXT NOT NULL DEFAULT 'logfire',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(run_id, chat_id, message_id)
)
"""

RECOVERY_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_recovery_attempts_run
    ON recovery_attempts(run_id)
"""

# One row per (chat, message): a message failed more than once has more than one
# tagged record, and the newest update is the one to replay.
WORKLIST_SQL = f"""
SELECT
    (attributes->'update'->'message'->'chat'->>'id')::bigint AS chat_id,
    (attributes->'update'->'message'->>'message_id')::bigint AS message_id,
    attributes->'update' AS update,
    max(start_timestamp) AS last_seen
FROM records
WHERE array_has(tags, '{FAILED_TAG}')
  AND attributes->'update'->'message'->>'message_id' IS NOT NULL
  AND attributes->'update'->'message'->'chat'->>'id' IS NOT NULL
GROUP BY 1, 2, 3
ORDER BY last_seen
"""

# Terminal outcomes never reach Telegram. Kept as one set so the dry-run table
# and the apply pass agree on what "terminal" means.
#
# `terminal_moderation_disabled` is the one to read carefully: it is NOT an
# error and NOT a lost message. `get_and_check_group` refuses a group whose
# `moderation_enabled` is false, so the production path would never classify
# these either — replaying them is a designed no-op until the group is
# re-enabled. Measured 2026-10-02: both chats holding the bulk of the backlog
# were paused when their admin's credit balance reached zero.
TERMINAL_OUTCOMES = frozenset(
    {
        "terminal_gone",
        "terminal_unresolvable",
        "terminal_no_update",
        "terminal_already_moderated",
        "terminal_no_user",
        "terminal_moderation_disabled",
        "terminal_group_missing",
        "terminal_skipped",
        "terminal_error",
    }
)

# Early exits the production pipeline reaches before classification. Mapped to
# their own outcome so a designed skip is never reported as a failure.
SKIP_OUTCOMES = {
    "message_moderation_disabled": "terminal_moderation_disabled",
    "error_message_group_not_found": "terminal_group_missing",
    "message_from_admin_skipped": "terminal_skipped",
    "message_trusted_member_skipped": "terminal_skipped",
    "message_from_channel_bot_skipped": "terminal_skipped",
}

# Terminal outcomes that PROVE the row is not a live loss: the message is gone
# from the group, the group is out of reach, or group policy skips it by design.
# A row closed with one of these stops counting toward the loss-watch's
# `status='failed' AND moderated_at IS NULL` backlog, whose own reading is "a
# message is sitting in a real group, unmoderated". The rest stay visible ON
# PURPOSE: `terminal_unresolvable` (we could not tell) and `terminal_error`
# (transient) must keep showing up as possible losses until a later pass settles
# them, and `terminal_moderation_disabled` is a chat whose operator turned
# moderation off — its messages are still sitting there, unmoderated.
CLOSE_OUTCOMES = frozenset(
    {"terminal_gone", "terminal_skipped", "terminal_group_missing"}
)


async def ensure_recovery_table() -> None:
    """Create the audit table. Idempotent, safe to run on every invocation."""
    from app.database.postgres_connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(RECOVERY_TABLE_DDL)
        await conn.execute(RECOVERY_INDEX_DDL)


async def load_store_state(keys: list[tuple[int, int]]) -> dict[tuple[int, int], dict]:
    """Current verdict-store state for the given keys.

    Read in one query: the work list comes from Logfire, and a row's DB state is
    what decides whether replay is a no-op (``moderated_at`` set), a retry
    (``failed``) or a fresh classification (``absent``).
    """
    if not keys:
        return {}
    from app.database.postgres_connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT chat_id, message_id, status, attempts, moderated_at, is_spam,
                   confidence, reason, result_id, created_at
            FROM classification_verdicts
            WHERE (chat_id, message_id) IN (
                SELECT * FROM unnest($1::bigint[], $2::bigint[])
            )
            """,
            [k[0] for k in keys],
            [k[1] for k in keys],
        )
    return {(r["chat_id"], r["message_id"]): dict(r) for r in rows}


async def fetch_worklist(lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> list[dict]:
    """Every distinct failed update Logfire holds, oldest first.

    The update outlives the DB row, so this list does NOT shrink as the 7-day
    verdict TTL reaps rows — which is the point: the DB query is a work list
    that quietly expires, Logfire is not.
    """
    try:
        from logfire.query_client import LogfireQueryClient
    except ImportError as e:  # pragma: no cover - logfire is a production dep
        raise SystemExit(f"logfire is not installed: {e}") from e

    token = os.getenv("LOGFIRE_READ_TOKEN")
    if not token:
        raise SystemExit("LOGFIRE_READ_TOKEN is required to read the failed updates")

    start = datetime.now(UTC) - timedelta(days=lookback_days)
    client = LogfireQueryClient(token)
    results = await asyncio.to_thread(
        client.query_json_rows,
        sql=WORKLIST_SQL,
        min_timestamp=start,
        limit=WORKLIST_LIMIT,
    )
    rows = list(results.get("rows") or [])
    if len(rows) >= WORKLIST_LIMIT:
        logger.warning(
            f"work list returned {len(rows)} rows, which equals the limit "
            f"{WORKLIST_LIMIT}: the result may be TRUNCATED. Count first, then read."
        )
    return rows


def reconstruct_message(update_json: Any) -> Any | None:
    """Rebuild the original ``Message`` from a Logfire update, or None.

    This is the same validation the dispatcher performs on a raw update, so the
    object handed to the handler is the original one — not a reconstruction we
    invent from the log line.
    """
    from aiogram import types

    if update_json is None:
        return None
    if isinstance(update_json, str):
        try:
            update_json = json.loads(update_json)
        except json.JSONDecodeError:
            return None
    if not isinstance(update_json, dict):
        return None
    try:
        update = types.Update.model_validate(update_json)
    except Exception as e:  # noqa: BLE001 - a malformed stored update is data, not a crash
        logger.debug(f"update failed validation: {e!r}")
        return None
    return update.message


async def message_present(client: Any, chat_id: int, message_id: int) -> bool | None:
    """Is the message still in the group? True / False / None (unknown).

    Offset-based, and deliberately so: ``offset_id = message_id + 1, limit=1``
    returns the highest id <= message_id, so PRESENT is an exact equality test.
    A min_id/max_id window returns empty for live messages and reads as "gone".
    None is a read error and is NOT collapsed into False — an unknown must never
    be moderated as if it were a decision.
    """
    try:
        result = await client.call(
            "messages.getHistory",
            params={
                "peer": chat_id,
                "offset_id": message_id + 1,
                "offset_date": 0,
                "add_offset": 0,
                "limit": 1,
                "max_id": 0,
                "min_id": 0,
                "hash": 0,
            },
            resolve=True,
            timeout=PRESENCE_TIMEOUT,
        )
    except Exception as e:  # noqa: BLE001 - any bridge failure is "unknown"
        logger.debug(f"presence check failed for {chat_id}/{message_id}: {e!r}")
        return None
    messages = result.get("messages") or []
    ids = [m.get("id") for m in messages]
    return bool(ids) and ids[0] == message_id


def plan_target(presence: bool | None, prior: dict | None) -> tuple[str, str]:
    """Decide what to do with one failed message. Pure: no I/O, no side effects.

    Returns ``(action, detail)`` with action in ``{"replay", "terminal"}``. This
    is the whole policy in one place so the dry-run table and the apply pass can
    never disagree about which rows are terminal:

    - a message an admin already handled (``moderated_at`` set) is NEVER
      moderated again, whatever its status says;
    - a message that is gone from the group cannot be moderated, so it is
      terminal — replaying it would only burn an LLM call and fail on the
      delete;
    - an unknown presence is terminal too, because "we could not tell" must not
      become "we deleted it";
    - otherwise the row is replayed through the production handler.
    """
    if prior is not None and prior.get("moderated_at") is not None:
        return "terminal", "terminal_already_moderated"
    if presence is True:
        return "replay", ""
    if presence is False:
        return "terminal", "terminal_gone"
    return "terminal", "terminal_unresolvable"


async def write_audit(run_id: str, record: dict) -> None:
    """Write one row per processed message. Idempotent on (run_id, chat, message)."""
    from app.database.postgres_connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO recovery_attempts (
                run_id, chat_id, message_id, outcome, detail, presence,
                prior_status, prior_attempts, prior_moderated_at, prior_created_at,
                is_spam, confidence, verdict_reason, result_id, moderated_at, source
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (run_id, chat_id, message_id) DO UPDATE SET
                outcome = EXCLUDED.outcome,
                detail = EXCLUDED.detail,
                presence = EXCLUDED.presence,
                prior_status = EXCLUDED.prior_status,
                prior_attempts = EXCLUDED.prior_attempts,
                prior_moderated_at = EXCLUDED.prior_moderated_at,
                prior_created_at = EXCLUDED.prior_created_at,
                is_spam = EXCLUDED.is_spam,
                confidence = EXCLUDED.confidence,
                verdict_reason = EXCLUDED.verdict_reason,
                result_id = EXCLUDED.result_id,
                moderated_at = EXCLUDED.moderated_at
            """,
            run_id,
            record["chat_id"],
            record["message_id"],
            record["outcome"],
            record.get("detail"),
            record.get("presence"),
            record.get("prior_status"),
            record.get("prior_attempts"),
            record.get("prior_moderated_at"),
            record.get("prior_created_at"),
            record.get("is_spam"),
            record.get("confidence"),
            record.get("verdict_reason"),
            record.get("result_id"),
            record.get("moderated_at"),
            record.get("source", "logfire"),
        )


async def audited_keys(run_id: str) -> set[tuple[int, int]]:
    """Keys already audited in this run, so a resumed run does not redo them."""
    from app.database.postgres_connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT chat_id, message_id FROM recovery_attempts WHERE run_id = $1",
            run_id,
        )
    return {(r["chat_id"], r["message_id"]) for r in rows}


async def credits_available(chat_id: int) -> bool:
    """Can this group pay for a moderation action?

    Read-only twin of ``deduct_credits_from_admins``: same join, same ordering,
    no UPDATE. The production path refuses to moderate without credits
    (``message_insufficient_credits``), so a dry-run that ignored the balance
    would over-state the delete list.
    """
    from app.database.constants import DELETE_PRICE
    from app.database.postgres_connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT max(a.credits) AS credits
            FROM administrators a
            JOIN group_administrators ga ON a.admin_id = ga.admin_id
            WHERE ga.group_id = $1
            """,
            chat_id,
        )
    return bool(row) and (row["credits"] or 0) >= DELETE_PRICE


async def classify_message(message: Any, admin_ids: list[int], topic: str | None):
    """The classification half of the production pipeline, and only that half.

    Mirrors ``pipeline._classify`` exactly (context collection, the story
    short-circuit, then the classifier) so a dry-run verdict is the verdict the
    apply pass would reach. It deliberately stops before moderation: this
    function has no way to delete anything.
    """
    from app.spam.message_context import collect_message_context
    from app.spam.spam_classifier import is_spam as classify_spam

    collected = await collect_message_context(message)
    ctx = collected.context
    if ctx is not None:
        ctx.chat_topics = topic
    if collected.is_story:
        return True, 100, "Story forward"
    return await classify_spam(
        comment=collected.message_text,
        admin_ids=admin_ids,
        context=ctx,
    )


async def early_exit_reason(message: Any) -> str:
    """The production early-exit check, read-only.

    Admin messages and trusted members never reach the classifier, so a dry-run
    that skipped this would predict deletions the pipeline would never make.
    Returns "" when the message should be classified.
    """
    from app.common.utils import determine_effective_user_id
    from app.handlers.message.validation import validate_group_and_check_early_exits

    user_id = determine_effective_user_id(message)
    if user_id is None:
        return "terminal_no_user"
    _group, reason = await validate_group_and_check_early_exits(
        message.chat.id,
        user_id,
        getattr(message.chat, "title", None),
        getattr(message.chat, "username", None),
    )
    return reason or ""


def verdict_class(is_spam: bool, confidence: int, threshold: int) -> str:
    """What the production path would DO with this verdict.

    Read off ``process_spam_or_approve``: only a confident spam verdict deletes;
    anything under the threshold goes to admin review instead.
    """
    if confidence < threshold:
        return "would_review"
    return "would_delete" if is_spam else "would_keep"


async def process_row(
    row: dict,
    prior: dict | None,
    *,
    run_id: str,
    threshold: int,
    mtproto: Any,
    apply: bool,
) -> dict:
    """Handle one failed message. Shared by both passes so they cannot drift.

    ``apply=False`` (the default) classifies, audits and returns — it writes
    nothing to Telegram and nothing to the verdict store. ``apply=True`` replays
    the update through ``handle_moderated_message``.
    """
    chat_id, message_id = row["chat_id"], row["message_id"]
    record: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "presence": None,
        "prior_status": (prior or {}).get("status"),
        "prior_attempts": (prior or {}).get("attempts"),
        "prior_moderated_at": (prior or {}).get("moderated_at"),
        "prior_created_at": (prior or {}).get("created_at"),
    }

    message = reconstruct_message(row.get("update"))
    if message is None:
        record.update(
            outcome="terminal_no_update", detail="no stored update in Logfire"
        )
        return record

    presence = await message_present(mtproto, chat_id, message_id)
    record["presence"] = {True: "present", False: "gone", None: "unknown"}[presence]

    action, detail = plan_target(presence, prior)
    if action == "terminal":
        record.update(outcome=detail, detail=detail)
        if apply and detail in CLOSE_OUTCOMES:
            await close_row(chat_id, message_id, detail)
        return record

    exit_reason = await early_exit_reason(message)
    if exit_reason:
        outcome = SKIP_OUTCOMES.get(exit_reason, "terminal_error")
        record.update(outcome=outcome, detail=f"early exit: {exit_reason}")
        if apply and outcome in CLOSE_OUTCOMES:
            await close_row(chat_id, message_id, outcome)
        return record

    # The ONLY branch that can reach Telegram. Everything above is read-only,
    # and the dry-run default never gets past this line: `replay` is the single
    # function in this module that imports the moderation handler, and this is
    # its only call site.
    if apply:
        return await replay(message, prior, record, run_id=run_id)

    return await classify_for_report(message, chat_id, record, threshold=threshold)


async def classify_for_report(
    message: Any, chat_id: int, record: dict, *, threshold: int
) -> dict:
    """The dry-run body: classify, label the outcome, touch nothing.

    Read-only by construction — the classifier and the balance check are reads,
    and this function has no reference to the moderation handler at all.
    """
    group = await _group_for(chat_id)
    admin_ids = list(group.admin_ids) if group else []
    topic = getattr(group, "topic_description_short", None) if group else None
    try:
        is_spam, confidence, reason = await classify_message(message, admin_ids, topic)
    except Exception as e:  # noqa: BLE001 - one bad row must not end the run
        record.update(outcome="terminal_error", detail=f"classify failed: {e!r}")
        return record
    outcome = verdict_class(is_spam, confidence, threshold)
    detail = ""
    if outcome == "would_delete" and not await credits_available(chat_id):
        outcome, detail = "would_review", "no credits: moderation would be refused"
    record.update(
        outcome=outcome,
        detail=detail or None,
        is_spam=is_spam,
        confidence=confidence,
        verdict_reason=reason,
    )
    return record


async def _group_for(chat_id: int):
    from app.database.group_operations import get_group

    return await get_group(chat_id)


async def max_attempts() -> int:
    """The configured retry cap. Read per call, because `load_config()` re-reads."""
    from app.common.utils import load_config
    from app.database import DEFAULT_VERDICT_RETRY_ATTEMPTS

    return int(
        load_config()
        .get("cache", {})
        .get("verdict_retry_attempts", DEFAULT_VERDICT_RETRY_ATTEMPTS)
    )


async def reset_attempts(chat_id: int, message_id: int) -> None:
    """Clear the retry counter for a row being replayed.

    `load_config()` re-reads ``config.yaml`` on EVERY call, so the cap cannot be
    overridden in memory: without this a row at the cap makes `reclaim_failed`
    refuse and the replay silently no-ops. The counter is not bypassed or
    duplicated — the row is put back inside the existing predicate's range, and
    the reset is recorded in the audit row written just before it.
    """
    from app.database.postgres_connection import get_pool

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE classification_verdicts
            SET attempts = 0
            WHERE chat_id = $1 AND message_id = $2
            """,
            chat_id,
            message_id,
        )


async def close_row(chat_id: int, message_id: int, reason: str) -> None:
    """Close the verdict row so it stops counting as a live loss.

    Reached ONLY in apply mode, and only for an outcome in ``CLOSE_OUTCOMES``.
    The store's ``closed_at IS NULL`` guard makes it idempotent, so a second
    pass over the same row is a no-op and the loss-watch's backlog cannot shrink
    twice for one message.
    """
    from app.database.classification_verdicts import close_verdict

    await close_verdict(chat_id, message_id, reason)

async def drain_inflight(timeout: float = 300.0) -> None:
    """Wait for the detached verdict tasks this row started.

    `_detach` runs the classification in its own task, so the handler can return
    while the work is still finishing. Draining before the audit is read keeps
    the batch sequential and the audit exact instead of racing the task.
    """
    import time as _time

    from app.handlers.message.verdict import inflight_tasks

    deadline = _time.monotonic() + timeout
    while True:
        tasks = inflight_tasks()
        if not tasks:
            return
        left = deadline - _time.monotonic()
        if left <= 0:
            logger.warning(
                f"drain_inflight timed out with {len(tasks)} task(s) still running"
            )
            return
        await asyncio.wait(tasks, timeout=min(left, 30.0))


async def replay(
    message: Any, prior: dict | None, record: dict, *, run_id: str
) -> dict:
    """Replay the original update through the production handler.

    `handle_moderated_message(message, source="new")` is the same entry point the
    webhook reaches. With the row still ``failed``, the existing gate does the
    rest — reclaim, classify, store, claim, moderate — so exactly-once is the
    store's `moderated_at IS NULL` guard and not new logic here.
    """
    from app.common.trace_context import set_webhook_deadline
    from app.handlers.message.pipeline import handle_moderated_message

    chat_id, message_id = message.chat.id, message.message_id
    cap = await max_attempts()
    if int((prior or {}).get("attempts") or 0) >= cap:
        await reset_attempts(chat_id, message_id)
        record["detail"] = (
            f"attempts reset {(prior or {}).get('attempts')} -> 0 (cap {cap})"
        )

    # Not a webhook turn: a generous ceiling so `_detach` waits for the
    # classification instead of answering PENDING mid-batch.
    set_webhook_deadline(RECOVERY_TURN_SECONDS)
    try:
        result_id = await handle_moderated_message(message, source="new")
    except Exception as e:  # noqa: BLE001 - one bad row must not end the run
        record.update(outcome="terminal_error", detail=f"replay raised: {e!r}")
        return record

    await drain_inflight()
    after = await load_store_state([(chat_id, message_id)])
    row = after.get((chat_id, message_id)) or {}
    record.update(
        outcome="recovered",
        result_id=result_id,
        is_spam=row.get("is_spam"),
        confidence=row.get("confidence"),
        verdict_reason=row.get("reason"),
        moderated_at=row.get("moderated_at"),
    )
    return record


def render_table(records: list[dict], run_id: str, apply: bool) -> str:
    """The review table: per-chat counts, the delete list, and the TTL clock."""
    from app.database import DEFAULT_VERDICT_TTL_DAYS

    ttl = timedelta(days=DEFAULT_VERDICT_TTL_DAYS)
    soon = datetime.now(UTC) + timedelta(hours=48)
    mode = "APPLY" if apply else "DRY-RUN"
    by_chat: dict[int, Counter] = {}
    expiring: Counter = Counter()
    deletes: dict[int, list[tuple[int, int]]] = {}

    def _bucket(r: dict) -> str:
        """One column per record. ``recovered`` is ambiguous on its own: in apply
        mode a recovery is a deletion when ``is_spam`` and an approval otherwise,
        so it is split here rather than dumped into the delete column."""
        outcome = r["outcome"]
        if outcome == "recovered":
            return "deleted" if r.get("is_spam") else "kept"
        if outcome == "would_delete":
            return "deleted"
        if outcome == "would_keep":
            return "kept"
        if outcome == "would_review":
            return "review"
        return "terminal"

    for r in records:
        counts = by_chat.setdefault(r["chat_id"], Counter())
        bucket = _bucket(r)
        counts[bucket] += 1
        if r.get("prior_created_at") and r["prior_created_at"] + ttl <= soon:
            expiring[r["chat_id"]] += 1
        if bucket == "deleted":
            deletes.setdefault(r["chat_id"], []).append(
                (r["message_id"], r.get("confidence") or 0)
            )

    total = Counter()
    for counts in by_chat.values():
        total.update(counts)

    def row_for(label: str, counts: Counter, exp: int = 0) -> str:
        return (
            f"{label:<22} {counts.get('deleted', 0):>6} "
            f"{counts.get('review', 0):>6} {counts.get('kept', 0):>6} "
            f"{counts.get('terminal', 0):>6} {exp:>7}"
        )

    lines = [
        f"recovery run {run_id}  mode={mode}  rows={len(records)}",
        "",
        f"{'chat':<22} {'delete':>6} {'review':>6} {'keep':>6} {'term':>6} {'exp<48h':>7}",
        row_for("TOTAL", total, sum(expiring.values())),
    ]
    for chat_id, counts in sorted(by_chat.items(), key=lambda kv: -sum(kv[1].values())):
        lines.append(row_for(str(chat_id), counts, expiring.get(chat_id, 0)))

    terminal_detail: Counter = Counter()
    for r in records:
        if r["outcome"] in TERMINAL_OUTCOMES:
            terminal_detail[r["outcome"]] += 1
    if terminal_detail:
        lines.append("")
        lines.append("terminal breakdown (no Telegram action by design):")
        for outcome, n in terminal_detail.most_common():
            lines.append(f"  {outcome:<34} {n:>5}")

    lines.append("")
    lines.append(
        "exp<48h = the verdict row expires within 48 h (7-day TTL on created_at). "
        "The update survives in Logfire, so recovery stays possible after expiry."
    )
    for chat_id, ids in sorted(deletes.items()):
        lines.append("")
        heading = "deleted (apply)" if apply else "delete list"
        lines.append(f"{heading} for {chat_id} ({len(ids)}):")
        lines.append("  " + ", ".join(f"{m}(c{c})" for m, c in sorted(ids)))

    problems = [r for r in records if r["outcome"] == "terminal_error"]
    if problems:
        lines.append("")
        lines.append(f"errors ({len(problems)}):")
        for r in problems[:20]:
            lines.append(f"  {r['chat_id']}/{r['message_id']}: {r.get('detail')}")
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    """Fetch the work list, process every row, audit each, print the table."""
    from app.common.mtproto_client import MtprotoHttpClient
    from app.common.utils import load_config

    await ensure_recovery_table()

    run_id = args.run_id or (
        f"recover-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    )
    rows = await fetch_worklist(args.lookback_days)
    if args.chat:
        wanted = set(args.chat)
        rows = [r for r in rows if r["chat_id"] in wanted]

    keys = [(r["chat_id"], r["message_id"]) for r in rows]
    store = await load_store_state(keys)

    already = await audited_keys(run_id)
    if already:
        rows = [r for r in rows if (r["chat_id"], r["message_id"]) not in already]
        logger.info(f"resuming {run_id}: {len(already)} row(s) already audited")
    if args.limit:
        rows = rows[: args.limit]

    if not rows:
        print(f"recovery run {run_id}: nothing to do")
        return 1

    threshold = int(load_config().get("spam", {}).get("high_confidence_threshold", 90))
    mtproto = MtprotoHttpClient.from_env()
    records: list[dict] = []

    for i, row in enumerate(rows, 1):
        prior = store.get((row["chat_id"], row["message_id"]))
        record = await process_row(
            row,
            prior,
            run_id=run_id,
            threshold=threshold,
            mtproto=mtproto,
            apply=args.apply,
        )
        await write_audit(run_id, record)
        records.append(record)
        if i % 10 == 0 or i == len(rows):
            print(f"  ..{i}/{len(rows)}", flush=True)

    print(render_table(records, run_id, args.apply))
    print(f"\naudit: SELECT * FROM recovery_attempts WHERE run_id = '{run_id}'")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay failed classifications from Logfire (dry-run by default)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="replay through handle_moderated_message; without it, classify and report only",
    )
    parser.add_argument("--run-id", default=None, help="resume/identify a run")
    parser.add_argument("--limit", type=int, default=0, help="process at most N rows")
    parser.add_argument(
        "--chat", type=int, action="append", default=None, help="only this chat"
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="how far back to read the Logfire work list",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from app.common.llm_budget import validate_llm_config
    from app.logging_setup import setup_logging

    args = parse_args(argv)
    setup_logging()
    validate_llm_config()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:  # pragma: no cover - operator interrupt
        return 130


if __name__ == "__main__":
    sys.exit(main())
