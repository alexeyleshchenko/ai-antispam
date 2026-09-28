"""Guard for the stale-pending reaper cadence (issue #48).

MEASURED DEFECT: `cleanup_stale_pending_verdicts` deletes `pending` rows older
than a 15-minute threshold, but it ran only from the DAILY bundle — an 86400-
second loop against a 900-second predicate, a 96x mismatch. A restart orphans a
`pending` row (the detached classification task dies with the process), the boot
sweep sees a row younger than the threshold and skips it, and the next sweep is
a day away. Every redelivery of that update answered 503 meanwhile:
`pending_update_count` reached 14, the health probe stayed ALERT, and one
message sat unmoderated until the row was reaped by hand.

The cadence is the fix, so the cadence is what is guarded:

  1. the shortest interval between two reaps fits the threshold the reaper
     enforces — the property whose violation WAS the defect;
  2. that interval is DERIVED from the configured threshold, not a literal that
     can drift away from it;
  3. the loop reaps BEFORE its first sleep, because a restart is the measured
     orphan case and a sleep-first loop leaves it uncovered for a full cadence;
  4. the daily bundle no longer reaps, since that cadence is the defect.

Leg 1 computes the interval through a fallback to the daily loop when the
dedicated cadence function is absent, so on the pre-fix revision it is a real
assertion failure ("86400 fits 900" is false) rather than an ImportError about a
missing name.
"""

import asyncio
import types
from unittest.mock import AsyncMock

import pytest

from app.background_jobs import scheduled_tasks as st


def _threshold_seconds() -> int:
    return st._get_cache_ttl_days()["verdict_pending_stale_minutes"] * 60


def _min_inter_reap_seconds() -> int:
    """Shortest gap between two reaps, per the code as it stands.

    Pre-fix there is no dedicated loop, so the only code path that reaps is the
    daily bundle — and its interval is SECONDS_PER_DAY. Reporting that here is
    what turns the fail-first run into an assertion about the defect.
    """
    cadence = getattr(st, "stale_pending_cadence_seconds", None)
    if cadence is not None:
        return cadence()
    return st.SECONDS_PER_DAY


def test_reap_cadence_fits_the_threshold_it_enforces():
    threshold = _threshold_seconds()
    interval = _min_inter_reap_seconds()

    assert interval <= threshold, (
        f"reap cadence {interval}s does not fit the staleness threshold "
        f"{threshold}s: a stranded `pending` row can answer 503 for up to one "
        f"full cadence before anything reaps it (issue #48)"
    )


def test_cadence_is_derived_from_the_configured_threshold(monkeypatch):
    def _cfg(minutes: int) -> dict:
        return {"verdict_pending_stale_minutes": minutes}

    monkeypatch.setattr(st, "_get_cache_ttl_days", lambda: _cfg(30))
    wide = st.stale_pending_cadence_seconds()
    monkeypatch.setattr(st, "_get_cache_ttl_days", lambda: _cfg(15))
    narrow = st.stale_pending_cadence_seconds()

    assert wide == 600, f"a 30-minute threshold should give 600s, got {wide}"
    assert narrow == 300, f"a 15-minute threshold should give 300s, got {narrow}"
    assert wide != narrow, "cadence is a literal — it cannot follow the threshold"


async def test_reaps_before_the_first_sleep(monkeypatch):
    calls: list[tuple[str, int]] = []

    async def _fake_reap(minutes: int = 15) -> int:
        calls.append(("reap", minutes))
        return 0

    async def _fake_sleep(seconds: int) -> None:
        calls.append(("sleep", seconds))
        raise asyncio.CancelledError

    monkeypatch.setattr(st, "cleanup_stale_pending_verdicts", _fake_reap)
    monkeypatch.setattr(
        st,
        "asyncio",
        types.SimpleNamespace(sleep=_fake_sleep, CancelledError=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await st.stale_pending_reaper_loop()

    assert [name for name, _ in calls] == ["reap", "sleep"], (
        f"the loop must reap before sleeping (boot case), got {calls}"
    )
    assert calls[1][1] <= _threshold_seconds(), (
        f"the sleep {calls[1][1]}s exceeds the {_threshold_seconds()}s threshold"
    )


async def test_the_daily_bundle_no_longer_reaps(monkeypatch):
    reap = AsyncMock()
    monkeypatch.setattr(st, "cleanup_stale_pending_verdicts", reap)

    for name in (
        "run_low_balance_checks",
        "leave_no_rights_groups",
        "cleanup_old_lookup_entries",
        "cleanup_old_message_history",
        "cleanup_pending_spam_examples",
        "cleanup_old_verdicts",
        "heal_bare_group_rows",
        "check_unmoderated_paid_groups",
    ):
        monkeypatch.setattr(st, name, AsyncMock())

    await st.run_scheduled_jobs()

    reap.assert_not_awaited()
