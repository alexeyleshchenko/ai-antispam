"""
Unified scheduled jobs: low balance warnings, cache cleanups.

Runs daily. Replaces the former low_balance_loop with a single loop that:
1. Sends low balance / depletion timeline notifications
2. Leaves no-rights groups past their grace period
3. Cleans message_lookup_cache (configurable TTL)
4. Cleans message_history (configurable TTL)
5. Cleans pending spam_examples (configurable TTL)
6. Heals bare group rows
7. Reports paying groups left silently unmoderated (issue #41)

The stale-pending verdict reaper is deliberately NOT in this bundle: its
predicate is a 15-minute threshold, so an 86400-second cadence let a row
orphaned by a restart answer 503 for up to a day (issue #48). It runs in its
own loop whose cadence is DERIVED from that threshold - see
`stale_pending_reaper_loop`.
"""

import asyncio
import logging

from ..common.utils import load_config
from ..database.classification_verdicts import (
    cleanup_old_verdicts,
    cleanup_stale_pending_verdicts,
)
from ..database.group_operations import heal_bare_group_rows
from ..database.message_lookup import cleanup_old_lookup_entries
from ..database.message_operations import cleanup_old_message_history
from ..database.spam_examples import cleanup_pending_spam_examples
from .low_balance import run_low_balance_checks
from .moderation_monitor import check_unmoderated_paid_groups
from .no_rights import leave_no_rights_groups

logger = logging.getLogger(__name__)
SECONDS_PER_DAY = 86400


def _get_cache_ttl_days() -> dict[str, int]:
    """Load cache TTL config. Returns dict with message_lookup, message_history, pending_spam."""
    cfg = load_config().get("cache", {})
    return {
        "message_lookup": cfg.get("message_lookup_ttl_days", 7),
        "message_history": cfg.get("message_history_ttl_days", 1),
        "pending_spam": cfg.get("pending_spam_ttl_days", 7),
        "verdict_ttl_days": cfg.get("verdict_ttl_days", 7),
        "verdict_pending_stale_minutes": cfg.get("verdict_pending_stale_minutes", 15),
    }


async def run_scheduled_jobs() -> None:
    """Run all scheduled jobs: low balance checks and cache cleanups."""
    try:
        await run_low_balance_checks()
    except Exception:
        logger.exception("Low balance checks failed")

    try:
        await leave_no_rights_groups()
    except Exception:
        logger.exception("No-rights group leave failed")

    ttl = _get_cache_ttl_days()
    try:
        await cleanup_old_lookup_entries(days=ttl["message_lookup"])
    except Exception:
        logger.exception("message_lookup_cache cleanup failed")

    try:
        await cleanup_old_message_history(days=ttl["message_history"])
    except Exception:
        logger.exception("message_history cleanup failed")

    try:
        await cleanup_pending_spam_examples(days=ttl["pending_spam"])
    except Exception:
        logger.exception("pending spam_examples cleanup failed")

    try:
        await cleanup_old_verdicts(days=ttl["verdict_ttl_days"])
    except Exception:
        logger.exception("classification_verdicts cleanup failed")

    try:
        await heal_bare_group_rows()
    except Exception:
        logger.exception("bare group rows heal failed")

    # Last: reports the SETTLED state, after the jobs above may have paused or
    # reactivated groups (issue #41).
    try:
        await check_unmoderated_paid_groups()
    except Exception:
        logger.exception("moderation monitor failed")


async def scheduled_jobs_loop() -> None:
    """Background loop: run jobs every 24 hours. Cancel on shutdown."""
    while True:
        try:
            await run_scheduled_jobs()
        except asyncio.CancelledError:
            logger.info("Scheduled jobs loop cancelled")
            raise
        except Exception:
            logger.exception("Scheduled jobs loop error")
        await asyncio.sleep(SECONDS_PER_DAY)


def stale_pending_cadence_seconds() -> int:
    """Cadence for the stale-pending reaper, DERIVED from its own threshold.

    Derived rather than a literal on purpose. The reaper's predicate is "older
    than the threshold", so a cadence LONGER than the threshold lets a stranded
    row outlive the threshold by a whole tick - which is the defect exactly: a
    15-minute threshold reaped on an 86400-second loop is a 96x mismatch, and a
    row orphaned by a restart answered 503 until the next daily run. A literal
    would re-create that drift the moment someone edits
    `verdict_pending_stale_minutes`; deriving it makes the two unable to
    disagree. The floor of 60 s only guards against a nonsensical config.
    """
    minutes = _get_cache_ttl_days()["verdict_pending_stale_minutes"]
    return max(60, minutes * 60 // 3)


async def stale_pending_reaper_loop() -> None:
    """Reap stranded `pending` verdicts on a cadence that fits the threshold.

    Reaps BEFORE the first sleep. A restart is the very thing that orphans a
    row - the detached classification task dies with the process - so a loop
    that slept first would leave the boot case uncovered for a full cadence,
    which is the case the defect was measured on.

    Ownership needs no marker and no replica assumption: a `pending` row older
    than the threshold cannot have a live owner, because the detached task is
    bounded well below it (see `cleanup_stale_pending_verdicts`). Reaping on
    that predicate is therefore safe however many processes run.
    """
    while True:
        try:
            minutes = _get_cache_ttl_days()["verdict_pending_stale_minutes"]
            await cleanup_stale_pending_verdicts(minutes=minutes)
        except asyncio.CancelledError:
            logger.info("Stale pending reaper loop cancelled")
            raise
        except Exception:
            logger.exception("stale pending classification_verdicts cleanup failed")
        await asyncio.sleep(stale_pending_cadence_seconds())
