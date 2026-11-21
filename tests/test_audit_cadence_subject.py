#!/usr/bin/env python3
"""Gate: the cadence clock is fed ONLY by self-audit rows, and it NAMES a hole between them.

WHY THIS FILE EXISTS (#124). The cadence leg read `event in ("run","score")` with no subject
predicate, so ANY run row fed the self-audit clock. Measured 2026-10-25..27: the `#123`
correction row (n=267) was the newest run row for three days, so the leg reported HELD while
three daily handoffs went unwritten. The defect is not that the clock was stale -- it is that
an unrelated row could satisfy it at all.

TWO LEGS, and each one fails on the PRE-FIX code for a different reason:

* **A -- the hole is NAMED.** Rows at D-4 and D, with scorecards D-4..D. The clock is FRESH
  (D is today), so staleness cannot find anything: the D-3..D-1 handoffs are invisible to it.
  The leg must report a `hole` naming those days, not `held`. A pre-fix leg returns `held`
  here, which is the false green this issue was filed for.
* **B -- the control: an unrelated row is NOT the clock.** The only recent row carries a board
  subject (`#N`). With no self-audit row inside the threshold, the leg must read STALE. A
  pre-fix leg reads that board row and reports HELD -- exactly what n=267 did for three days.

Leg B is the control AGENTS.md rule 7 requires: it is the arm that would have caught the real
incident, and it is discriminating because A and B differ ONLY in the row's subject.

Runs as a script (`python3 tests/test_audit_cadence_subject.py`), the convention this repo's
gates follow.
"""

from __future__ import annotations

import datetime
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import audit

SELF_AUDIT = "internal-self-audit"
# Staleness threshold the leg uses is 30h; 40h is unambiguously past it.
STALE_HOURS = 40.0


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _row(n: int, hours_ago: float, subject: str, event: str = "run") -> dict:
    ts = (_utcnow() - datetime.timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")
    return {"n": n, "ts": ts, "event": event, "actor": "hq", "subject": subject, "detail": "fixture"}


def _fixture(root: Path, rows: list[dict], artifact_days_ago: list[float]) -> tuple[Path, Path]:
    """A throwaway `(ledger, scores)` pair with exact control over both series."""
    ledger = root / "evidence" / "ledger.jsonl"
    scores = root / "evidence" / "scores"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    scores.mkdir(parents=True, exist_ok=True)
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    for days in artifact_days_ago:
        stamp = (_utcnow() - datetime.timedelta(days=days)).date().isoformat()
        (scores / f"{stamp}-self-audit.md").write_text("# fixture scorecard\n", encoding="utf-8")
    return ledger, scores


def leg_hole_is_named(problems: list[str]) -> None:
    """A: fresh clock, healthy endpoints, a hole in the middle -- the leg must name it."""
    with tempfile.TemporaryDirectory() as tmp:
        rows = [_row(1, 4 * 24, SELF_AUDIT), _row(2, 0.0, SELF_AUDIT)]
        arts = [4.0, 3.0, 2.0, 1.0, 0.0]
        ledger, scores = _fixture(Path(tmp), rows, arts)
        stats = audit.check_cadence_integrity(ledger, scores)

    if stats.get("state") != "hole":
        problems.append(
            f"A[hole_named]: expected state 'hole' (fresh clock, endpoints healthy, D-3..D-1 "
            f"unwritten), got {stats.get('state')!r}"
        )
    holes = stats.get("holes") or []
    want = sorted((_utcnow() - datetime.timedelta(days=d)).date().isoformat() for d in (3.0, 2.0, 1.0))
    if holes != want:
        problems.append(f"A[hole_days]: expected holes {want}, got {holes}")
    if stats.get("cadence_held"):
        problems.append(
            "A[not_held]: a hole reported cadence_held=True; an unwritten handoff is not a healthy leg"
        )
    label = audit.render_cadence_label(stats)
    if "HOLE" not in label:
        problems.append(f"A[label]: the hole state renders {label!r}, which does not name it")
    # The label renders the SPAN (`HOLE D-3..D-1`); the per-day list is the `holes` field.
    # Both endpoints must appear, or the reader cannot bound the debt from the label alone.
    if holes and (holes[0] not in label or holes[-1] not in label):
        problems.append(
            f"A[label_days]: the label {label!r} does not bound the hole {holes[0]}..{holes[-1]}"
        )


def leg_unrelated_row_is_not_the_clock(problems: list[str]) -> None:
    """B: the control. A board row must not satisfy the self-audit clock.

    The fixture is built so that a HOLE cannot mask this leg: the only scorecard is dated the
    day of the self-audit row, so `holes` is empty and `state` is decided by the clock alone.
    Without that, a pre-fix leg still reports `hole` (its hole logic reads the same rows) and
    `cadence_held` is already False -- the control passes on broken code, which is exactly what
    the first draft of this file did.
    """
    with tempfile.TemporaryDirectory() as tmp:
        rows = [_row(1, STALE_HOURS, SELF_AUDIT), _row(2, 1.0, "#123")]
        # Artifacts ONLY on the self-audit row's own day: nothing is missing, so nothing is a hole.
        arts = [STALE_HOURS / 24.0]
        ledger, scores = _fixture(Path(tmp), rows, arts)
        stats = audit.check_cadence_integrity(ledger, scores)

    if stats.get("holes"):
        problems.append(
            f"B[fixture]: the control carries holes {stats.get('holes')}, so a hole can mask the "
            f"clock; the fixture must isolate the subject"
        )
    if stats.get("cadence_held"):
        problems.append(
            f"B[control]: an unrelated '#123' run row 1h ago satisfied the clock (state "
            f"{stats.get('state')!r}); this is the n=267 incident, and the leg must not repeat it"
        )
    if stats.get("state") == "held":
        problems.append("B[control]: the leg reported HELD with no recent self-audit row")
    if stats.get("last_run_ts") is None:
        problems.append("B[record]: no self-audit row was read at all; the stale row should still be found")


def leg_subject_bound_in_source(problems: list[str]) -> None:
    """The predicate itself: a future edit must not silently widen it back."""
    src = (REPO / "tools" / "audit.py").read_text(encoding="utf-8")
    if "SELF_AUDIT_SUBJECT" not in src:
        problems.append("source: tools/audit.py declares no SELF_AUDIT_SUBJECT; the clock is unbound")
        return
    # The defect's exact shape: a bare event test feeding last_ts with no subject gate.
    for line in src.splitlines():
        if "last_ts = data.get" in line or "last_ts = ts" in line:
            continue
    if 'data.get("event") in ("run", "score")' in src:
        problems.append(
            'source: the pre-#124 predicate `data.get("event") in ("run", "score")` is still '
            "present; the clock can be fed by any row again"
        )


def _variant_subject(day_offset: float = 0.0) -> str:
    """The shape the by-hand cron runner actually wrote (ledger n=488, 2026-11-19)."""
    day = (_utcnow() - datetime.timedelta(days=day_offset)).date().isoformat()
    return f"daily self-audit {day}"


def leg_variant_subject_is_the_clock(problems: list[str]) -> None:
    """C: the OBSERVED VARIANT spelling feeds the clock -- the #159 regression arm.

    The cron route forbids `--stamp`, so the runner writes its run row by hand and the subject
    is FREE TEXT; ledger n=488 carries `daily self-audit 2026-11-19` while 64 other rows carry
    the canonical literal. The fixture puts a STALE canonical row beside a FRESH variant row:
    a reader that only knows the literal picks the stale one and reads MISSED, so this arm is
    RED on the pre-#159 code and GREEN once the predicate recognises the shape.
    """
    with tempfile.TemporaryDirectory() as tmp:
        rows = [_row(1, STALE_HOURS, SELF_AUDIT), _row(2, 1.0, _variant_subject())]
        # Artifact ONLY on the stale row's own day: nothing is missing, so no hole can decide
        # the state and the arm isolates the SUBJECT (the same control shape as leg B).
        arts = [STALE_HOURS / 24.0]
        ledger, scores = _fixture(Path(tmp), rows, arts)
        stats = audit.check_cadence_integrity(ledger, scores)

    if stats.get("holes"):
        problems.append(
            f"C[fixture]: the variant arm carries holes {stats.get('holes')}; the fixture must "
            f"isolate the subject, not the hole set"
        )
    if not stats.get("cadence_held"):
        problems.append(
            f"C[variant_clock]: a FRESH `{_variant_subject()}` row did not satisfy the clock "
            f"(state {stats.get('state')!r}); the observed 2026-11-19 row (n=488) is unreadable "
            f"and the leg reports a false HOLE/DROPPED ROW for a run whose artifact PASSED (#159)"
        )
    if stats.get("state") != "held":
        problems.append(f"C[state]: expected 'held' from the fresh variant row, got {stats.get('state')!r}")


def leg_predicate_shape(problems: list[str]) -> None:
    """F: the predicate's accept/reject table, stated as data."""
    predicate = getattr(audit, "is_self_audit_subject", None)
    if predicate is None:
        problems.append(
            "F[predicate]: tools/audit.py defines no is_self_audit_subject; the reader is still an "
            "exact-match on one typed spelling and every free-text variant is unreadable (#159)"
        )
        return
    accept = [
        "internal-self-audit",
        "  internal-self-audit  ",
        "daily self-audit 2026-11-19",
        "  daily self-audit 2026-11-19 ",
        "Daily Self-Audit 2026-11-19",
    ]
    reject = [
        "self-audit-class",           # n=519: Triage's row about a CLASS of defects
        "daily self-audit",           # no date: a topic label, not a run's own name
        "daily self-audit 2026-11-19 extra",
        "self-audit",                 # a bare substring must never be enough
        "#159",
        "",
        None,
    ]
    for subject in accept:
        if not predicate(subject):
            problems.append(f"F[accept]: {subject!r} is a self-audit row and was rejected")
    for subject in reject:
        if predicate(subject):
            problems.append(
                f"F[reject]: {subject!r} is NOT a self-audit row and was accepted; a loose match "
                f"lets an unrelated row satisfy the clock -- a false pass (#124's class)"
            )


def leg_unrelated_class_row_is_not_the_clock(problems: list[str]) -> None:
    """D: the false-pass guard -- `self-audit-class` (n=519) must NOT feed the clock.

    The fixture is leg C's with the fresh subject swapped for Triage's `self-audit-class`. It is
    GREEN on the pre-#159 code (exact match) and must STAY green: it exists to red a NAIVE fix.
    The bite proof at the end monkeypatches the predicate to `"self-audit" in subject` and shows
    that same fixture then reads HELD -- so the arm discriminates rather than merely agreeing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        rows = [_row(1, STALE_HOURS, SELF_AUDIT), _row(2, 1.0, "self-audit-class")]
        arts = [STALE_HOURS / 24.0]
        ledger, scores = _fixture(Path(tmp), rows, arts)
        stats = audit.check_cadence_integrity(ledger, scores)

    if stats.get("cadence_held"):
        problems.append(
            f"D[false_pass]: the `self-audit-class` row (n=519) satisfied the self-audit clock "
            f"(state {stats.get('state')!r}); a genuinely missing audit day would then read HELD"
        )
    if stats.get("state") == "held":
        problems.append("D[false_pass]: the leg reported HELD with no self-audit row inside the threshold")

    # Bite proof: a naive contains-match over the SAME fixture must be caught by this arm.
    predicate = getattr(audit, "is_self_audit_subject", None)
    if predicate is not None:
        audit.is_self_audit_subject = lambda s: "self-audit" in str(s or "").lower()  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                rows = [_row(1, STALE_HOURS, SELF_AUDIT), _row(2, 1.0, "self-audit-class")]
                ledger, scores = _fixture(Path(tmp), rows, [STALE_HOURS / 24.0])
                naive = audit.check_cadence_integrity(ledger, scores)
        finally:
            audit.is_self_audit_subject = predicate  # type: ignore[assignment]
        if not naive.get("cadence_held"):
            problems.append(
                "D[bite]: a naive `'self-audit' in subject` predicate also rejects n=519, so this "
                "arm does not discriminate against the fix it exists to forbid"
            )


def leg_hole_bites_under_variant_spelling(problems: list[str]) -> None:
    """E: the hole gate still bites when the rows are spelled the VARIANT way.

    Leg A proves the hole is named for canonical rows; this proves widening the reader did not
    buy the hole away -- a genuinely missing day between two variant-spelled rows still reds.
    """
    with tempfile.TemporaryDirectory() as tmp:
        rows = [_row(1, 4 * 24, _variant_subject(4)), _row(2, 0.0, _variant_subject())]
        arts = [4.0, 3.0, 2.0, 1.0, 0.0]
        ledger, scores = _fixture(Path(tmp), rows, arts)
        stats = audit.check_cadence_integrity(ledger, scores)

    want = sorted((_utcnow() - datetime.timedelta(days=d)).date().isoformat() for d in (3.0, 2.0, 1.0))
    if stats.get("holes") != want:
        problems.append(
            f"E[hole_days]: variant-spelled endpoints, expected holes {want}, got {stats.get('holes')}"
        )
    if stats.get("state") != "hole":
        problems.append(f"E[state]: expected 'hole', got {stats.get('state')!r}")
    if stats.get("cadence_held"):
        problems.append("E[not_held]: a hole under variant spelling reported cadence_held=True")


def main() -> int:
    problems: list[str] = []
    leg_hole_is_named(problems)
    leg_unrelated_row_is_not_the_clock(problems)
    leg_subject_bound_in_source(problems)
    leg_variant_subject_is_the_clock(problems)
    leg_unrelated_class_row_is_not_the_clock(problems)
    leg_hole_bites_under_variant_spelling(problems)
    leg_predicate_shape(problems)

    print("Gate: cadence clock bound to the self-audit subject, and a hole between rows is named (#124)")
    print("  predicate: state 'hole' names the unwritten days; an unrelated #N run row never feeds the clock")
    print("  #159: the reader recognises BOTH lawful spellings -- `internal-self-audit` and `daily self-audit YYYY-MM-DD`")
    print("        -- and still rejects `self-audit-class`, a bare `daily self-audit`, and any other free text")
    if problems:
        print(f"FAIL: {len(problems)} violation(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("PASS: hole named with its days, unrelated row rejected by the clock, predicate bound,")
    print("      variant spelling (#159) recognised, `self-audit-class` rejected, hole still bites")
    return 0


if __name__ == "__main__":
    sys.exit(main())
