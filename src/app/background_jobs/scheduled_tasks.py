"""
Unified scheduled jobs: low balance warnings, cache cleanups.

Runs daily. Replaces the former low_balance_loop with a single loop that:
1. Sends low balance / depletion timeline notifications
2. Cleans message_lookup_cache (configurable TTL)
3. Cleans message_history (configurable TTL)
4. Cleans pending spam_examples (configurable TTL)
"""

import asyncio
import logging

from ..common.utils import load_config
from ..database.group_operations import heal_bare_group_rows
from ..database.message_lookup import cleanup_old_lookup_entries
from ..database.message_operations import cleanup_old_message_history
from ..database.spam_examples import cleanup_pending_spam_examples
from .low_balance import run_low_balance_checks
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
        await heal_bare_group_rows()
    except Exception:
        logger.exception("bare group rows heal failed")


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
