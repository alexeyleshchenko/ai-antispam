#!/usr/bin/env python3
"""Gate: the cadence leg must not count the CURRENT run's own scorecard as a missing row.

WHY THIS FILE EXISTS. The #124 fix bound the cadence clock to `subject == internal-self-audit`
and added hole detection: a day that carries a scorecard artifact but no self-audit ledger row
is named as a hole. That is right for every day EXCEPT the one the audit is standing on. A run
writes its artifact, and its proving row is written AFTER `check_cadence_integrity` returns --
so the artifact is on disk while the row is not, and the leg reads the run's own output as
evidence that the run never happened.

Measured 2026-11-03: `--report` produced a scorecard and read HELD; the follow-up `--stamp`
recomputed cadence, saw that same artifact with no matching row, and wrote
`verdict=DEGRADED`. The report said GREEN and the durable record said DEGRADED, for the same
run. Every day since the #124 fix (n>=269) carried the same false red, and it went unnoticed
because a red leg was the expected state while the older real holes were still in the window.

Two legs, because a fix that only silences the false positive would blind the leg entirely:

* **own_run** -- the run's own artifact, no row for it, `own_run_date` set to it: the leg must
  read HELD with no holes. This is the case that was broken.
* **prior_hole (control)** -- an artifact from an EARLIER day with no row for it: the leg must
  STILL name it. This is what separates "excluded the run's own output" from "stopped looking
  for holes", and it is the leg that would have caught a fix that simply skipped today's date
  from every comparison. Without it, deleting hole detection entirely would pass.
* **surface** (source) -- `check_cadence_integrity` must take `own_run_date`, and `main()` must
  PASS it. A parameter nobody passes is dead code, and the defect lives in the call site as
  much as in the function.

Runs as a script (`python3 tests/test_audit_cadence_own_run.py`, how tools/audit.py invokes it).
"""

from __future__ import annotations

import datetime
import json
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import audit  # noqa: E402  (the tool under test; explicit paths keep it off live state)

FRESH_HOURS = 1.0

# The days are derived from the clock, never hardcoded: the leg compares against
# `datetime.now()`, so a pinned date starts lying the moment the calendar moves past it --
# and it lies in the direction that PASSES (every artifact outside the 7-day lookback, no
# holes, HELD for the wrong reason).
NOW = datetime.datetime.now(datetime.timezone.utc)
OWN = NOW.date().isoformat()
PRIOR = (NOW.date() - datetime.timedelta(days=1)).isoformat()

def _row_ts(day: str, now: datetime.datetime) -> str:
    """A timestamp that is ON `day` and, for the newest row, fresh enough to satisfy the clock.

    Both bounds matter. The UPPER clamp (`day` 23:00) is what keeps a row on its own day: the
    naive `now - FRESH_HOURS` stamp teleports the PRIOR day's row onto today, which orphans
    PRIOR's artifact and manufactures the very hole this gate exists to distinguish from --
    that was the first version of this fixture, and it reddened the own_run leg for a reason
    that had nothing to do with the code under test. The LOWER clamp (`day` 00:00) covers a
    run at 00:xx UTC, where `now - FRESH_HOURS` has already fallen back into yesterday.
    """
    base = datetime.datetime.fromisoformat(f"{day}T00:00:00+00:00")
    candidate = now - datetime.timedelta(hours=FRESH_HOURS)
    return max(base, min(candidate, base.replace(hour=23))).isoformat().replace("+00:00", "Z")


def _row(n: int, day: str, now: datetime.datetime, subject: str = "internal-self-audit") -> dict:
    return {
        "n": n,
        "ts": _row_ts(day, now),
        "event": "run",
        "actor": "hq",
        "subject": subject,
        "detail": "fixture row",
    }


def build_fixture(root: Path, *, artifacts: list[str], rows: list[str]) -> tuple[Path, Path]:
    """A throwaway `(ledger, scores)` pair with EXACT artifact days and row days.

    Days are explicit rather than derived from a clock offset: this gate is about a specific
    date being the run's own, so the fixture must be able to place artifacts and rows on named
    days. Each row is stamped by `_row_ts`, which keeps it on its own day AND fresh enough
    (`FRESH_HOURS`) for the staleness clock -- so the only variable left is the hole set.
    Stamping the newest row to `now - FRESH_HOURS` without that clamp put the PRIOR row on
    today's date, orphaned PRIOR's artifact and reddened own_run for an unrelated reason.
    """
    ledger = root / "evidence" / "ledger.jsonl"
    scores = root / "evidence" / "scores"
    scores.mkdir(parents=True, exist_ok=True)
    for day in artifacts:
        (scores / f"{day}-self-audit.md").write_text("# fixture scorecard\n", encoding="utf-8")

    now = datetime.datetime.now(datetime.timezone.utc)
    lines = [json.dumps(_row(i + 1, day, now)) for i, day in enumerate(rows)]
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    return ledger, scores


def _cadence(ledger: Path, scores: Path, own: str, problems: list[str], case: str) -> dict:
    """`check_cadence_integrity(..., own_run_date=own)` -- or a NAMED problem, never a traceback."""
    try:
        return audit.check_cadence_integrity(ledger, scores, own_run_date=own)
    except TypeError as exc:
        problems.append(
            f"{case}: check_cadence_integrity does not accept own_run_date ({exc}); the leg "
            f"cannot exclude the run's own artifact and reds on its own output"
        )
        return {}


def leg_own_run(problems: list[str]) -> None:
    """The run's own artifact, no row for it: HELD, no holes. The broken case."""
    with tempfile.TemporaryDirectory() as tmp:
        ledger, scores = build_fixture(Path(tmp), artifacts=[PRIOR, OWN], rows=[PRIOR])
        stats = _cadence(ledger, scores, OWN, problems, "own_run")
    if not stats:
        return
    if stats.get("cadence_held") is not True:
        problems.append(
            f"own_run: expected cadence_held=True for a run whose own artifact has no row yet, "
            f"got {stats.get('cadence_held')!r} (state={stats.get('state')!r}, "
            f"holes={stats.get('holes')!r}) -- this is the false DEGRADED of 2026-11-03"
        )
    if stats.get("holes"):
        problems.append(
            f"own_run: the leg named {stats.get('holes')!r} as holes; a run's own artifact is "
            f"not a missing handoff, its row is written after this function returns"
        )
    # The exclusion must reach the HOLE SET ONLY. `artifact_latest_date` is the newest scorecard
    # ON DISK: it anchors the lookback window and tells `classify_stale_cadence` whether a run
    # happened. Excluding the own day from it as well shortens the window and turns today's
    # dropped handoff into a "missed run" -- the fix must not buy one false red by creating
    # another, and this leg is what makes that regression loud instead of silent.
    if stats.get("artifact_latest_date") != OWN:
        problems.append(
            f"own_run: artifact_latest_date is {stats.get('artifact_latest_date')!r}, expected "
            f"{OWN!r} -- it must report the newest scorecard ON DISK; the own-day exclusion "
            f"belongs to the hole set alone (it anchors the lookback window and names "
            f"dropped_row vs missed_run)"
        )


def leg_prior_hole(problems: list[str]) -> None:
    """CONTROL: an EARLIER artifact with no row must still be named, own_run_date notwithstanding."""
    with tempfile.TemporaryDirectory() as tmp:
        # Own day is clean (artifact + row); the PRIOR day has an artifact and no row.
        ledger, scores = build_fixture(Path(tmp), artifacts=[PRIOR, OWN], rows=[OWN])
        stats = _cadence(ledger, scores, OWN, problems, "prior_hole")
    if not stats:
        return
    holes = stats.get("holes") or []
    if PRIOR not in holes:
        problems.append(
            f"prior_hole: expected {PRIOR} to be named as a hole (artifact present, no row), "
            f"got holes={holes!r}, state={stats.get('state')!r} -- excluding the run's own day "
            f"must not stop the leg looking at every other day"
        )
    if stats.get("cadence_held") is not False:
        problems.append(
            f"prior_hole: expected cadence_held=False with a real hole present, "
            f"got {stats.get('cadence_held')!r}"
        )


def leg_surface(problems: list[str]) -> None:
    """Source: the parameter must exist AND be passed by the caller."""
    src = (REPO / "tools" / "audit.py").read_text(encoding="utf-8")
    if "own_run_date" not in src:
        problems.append(
            "surface: tools/audit.py does not mention own_run_date; the cadence leg cannot "
            "exclude the run's own artifact"
        )
        return
    if not re.search(r"check_cadence_integrity\([^)]*own_run_date\s*=", src, re.DOTALL):
        problems.append(
            "surface: no call site passes own_run_date= to check_cadence_integrity; a parameter "
            "nobody passes leaves the false DEGRADED in place"
        )


def main() -> int:
    problems: list[str] = []
    leg_own_run(problems)
    leg_prior_hole(problems)
    leg_surface(problems)

    print("Gate: the cadence leg excludes the CURRENT run's own scorecard (#124 class)")
    print("  predicate: own artifact with no row is not a hole; a PRIOR artifact with no row still is")
    if problems:
        print(f"FAIL: {len(problems)} violation(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("PASS: own-run excluded, prior hole still named, parameter present and passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
