"""Coverage heartbeat: attests that join observation was unbroken.

Pre-existing-member trust (#108) rests on a negative inference — *no join
event on record, therefore the sender was already here when the bot arrived.*
That inference is sound only while the bot actually WATCHED for joins over the
whole interval since it was added. Nothing else in the process can attest to
that: a restart, a hang or a deploy kills the observer without leaving a mark
in any row it was responsible for writing. A heartbeat does leave one, and the
gap between two beats is the only evidence the observation was continuous.

The loop is deliberately boring: beat, sleep, repeat. Every judgement about
what a gap MEANS lives in `beat_coverage`, so the two cannot drift apart.
"""

import asyncio
import logging

from ..database.trust_operations import (
    beat_coverage,
    get_coverage_beat_seconds,
    get_coverage_gap_seconds,
)

logger = logging.getLogger(__name__)

def coverage_heartbeat_cadence_seconds() -> int:
    """Heartbeat cadence: the configured beat, clamped below the gap threshold.

    The two are separate knobs and can silently contradict each other. A beat
    interval at or above the gap threshold makes EVERY beat look like a gap, so
    `continuous_since` would reset forever and pre-existing trust would never
    open — a config mistake that reads as "the feature does not work" rather
    than as a bad number. Clamping to a third of the threshold keeps at least
    three beats inside every window, so the loop cannot manufacture the gap it
    is supposed to detect. This mirrors `stale_pending_cadence_seconds`, which
    derives its cadence from its own threshold for the same reason.
    """
    beat = get_coverage_beat_seconds()
    gap = get_coverage_gap_seconds()
    return max(1, min(beat, gap // 3))

async def coverage_heartbeat_loop() -> None:
    """Beat coverage immediately, then on the cadence, until cancelled.

    Beats BEFORE the first sleep for the same reason the stale-pending reaper
    reaps first: a restart is the very thing that breaks continuity, so a loop
    that slept first would leave the boot — the case this exists to cover —
    unwatched for a whole cadence.

    A failed beat is logged and swallowed. The loop must outlive a transient
    database error, and a beat that never lands simply leaves coverage broken,
    which is the fail-closed direction: trust stays closed until observation
    demonstrably resumes.
    """
    while True:
        try:
            await beat_coverage()
        except asyncio.CancelledError:
            logger.info("Coverage heartbeat loop cancelled")
            raise
        except Exception:
            logger.exception(
                "Coverage heartbeat failed; pre-existing trust stays closed"
            )
        await asyncio.sleep(coverage_heartbeat_cadence_seconds())
