"""Tests for the coverage heartbeat (issue #108, close leg 3's instrument).

Pre-existing trust is granted only while join observation has been unbroken.
The heartbeat is what makes that claim falsifiable: a gap between beats means
the process was not watching, and `continuous_since` advances past it. These
tests pin both halves of that contract — a beat inside the threshold must
leave continuity ALONE (or the grant would never open), and a beat after a gap
must ADVANCE it (or a blind interval would pass as watched).
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from src.app.background_jobs import coverage_heartbeat as ch
from src.app.database.trust_operations import (
    beat_coverage,
    coverage_continuous_since,
    get_coverage_beat_seconds,
    get_coverage_gap_seconds,
)

@pytest.fixture
def src_pool(test_pool):
    """Point the `src.*` module instance at the test pool.

    The suite imports the app twice — as `app.*` (pytest.ini sets
    `pythonpath = src`) and as `src.app.*` — two module objects with two
    distinct `_pool` globals. The session-wide `patched_db_conn` patches the
    `app.*` one, which these tests never read.
    """
    from src.app.database import postgres_connection

    original = postgres_connection._pool
    postgres_connection._pool = test_pool
    yield
    postgres_connection._pool = original

def test_cadence_stays_below_the_gap_threshold():
    """A cadence at or above the threshold makes every beat look like a gap."""
    cadence = ch.coverage_heartbeat_cadence_seconds()
    gap = get_coverage_gap_seconds()

    assert cadence >= 1
    assert cadence < gap, (
        f"heartbeat cadence {cadence}s does not fit inside the {gap}s gap "
        "threshold: every beat would read as a gap and continuity could never "
        "hold long enough to grant pre-existing trust"
    )

def test_cadence_cannot_drift_past_a_shortened_threshold(monkeypatch):
    """The clamp, not the config, is what guarantees the fit.

    A beat interval configured ABOVE the threshold is exactly the mistake that
    would read as 'the feature does not work', so the cadence is clamped to a
    third of the threshold rather than trusted.
    """
    monkeypatch.setattr(ch, "get_coverage_beat_seconds", lambda: 9999)
    monkeypatch.setattr(ch, "get_coverage_gap_seconds", lambda: 300)

    assert ch.coverage_heartbeat_cadence_seconds() == 100

@pytest.mark.asyncio
async def test_beat_inside_the_threshold_leaves_continuity_unchanged(
    src_pool, patched_db_conn, clean_db
):
    """The healthy case: two close beats, continuity anchored at the first."""
    first = await beat_coverage()
    assert await coverage_continuous_since() == first

    second = await beat_coverage()

    # The second beat moved `last_beat_at` but NOT `continuous_since`.
    assert await coverage_continuous_since() == first
    assert second == first

@pytest.mark.asyncio
async def test_gap_advances_continuity_to_the_post_gap_beat(
    src_pool, patched_db_conn, clean_db
):
    """The gap case: continuity restarts at the beat that ends the blind interval."""
    first = await beat_coverage()
    assert await coverage_continuous_since() == first

    # Simulate a hang/restart: the last beat is now older than the threshold.
    stale = datetime.now(UTC) - timedelta(seconds=get_coverage_gap_seconds() + 60)
    async with clean_db.acquire() as conn:
        await conn.execute(
            "UPDATE coverage_state SET last_beat_at = $1 WHERE id = 1", stale
        )

    resumed = await beat_coverage()

    since = await coverage_continuous_since()
    assert since == resumed, "continuity must restart at the beat that ended the gap"
    assert since != first
    assert since > first

@pytest.mark.asyncio
async def test_loop_beats_before_its_first_sleep(monkeypatch):
    """A restart is the measured gap case, so the boot must be covered at once."""
    order: list[str] = []

    async def fake_beat() -> None:
        order.append("beat")

    async def fake_sleep(_seconds: float) -> None:
        order.append("sleep")
        raise asyncio.CancelledError

    monkeypatch.setattr(ch, "beat_coverage", fake_beat)
    monkeypatch.setattr(ch.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await ch.coverage_heartbeat_loop()

    assert order == ["beat", "sleep"]

@pytest.mark.asyncio
async def test_loop_survives_a_failing_beat(monkeypatch):
    """A transient store error must not kill the loop that attests coverage."""
    calls: list[int] = []

    async def flaky_beat() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("store unavailable")

    async def fake_sleep(_seconds: float) -> None:
        if len(calls) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(ch, "beat_coverage", flaky_beat)
    monkeypatch.setattr(ch.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await ch.coverage_heartbeat_loop()

    assert len(calls) == 2, "the loop must retry after a failed beat"

def test_default_beat_config_fits_the_default_gap():
    """The shipped defaults must already satisfy the invariant."""
    assert get_coverage_beat_seconds() < get_coverage_gap_seconds()
