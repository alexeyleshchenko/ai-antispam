#!/usr/bin/env python3
"""Gate: the cadence leg NAMES the record it reads, and separates a DROPPED ROW from a MISSED RUN.

WHY THIS FILE EXISTS (#116). The cadence leg read the LEDGER alone. The ledger records a
run's HANDOFF; the scorecard artifact (`evidence/scores/<date>-self-audit.md`) records the run
ITSELF. The two can diverge, and when they did the leg reported the wrong failure -- measured
2026-10-03: the artifact `dd16280` was committed, the owed `run` row never landed, and the
report read `MISSED: Last run: 39.9h ago`. A reader cannot tell that apart from "no audit ran
for 40 hours", and the two have opposite remedies (re-stamp the row vs. fix the pacemaker). The
mis-report is worse than a missing check: a dropped handoff on a day the audit already reds for
another reason is invisible, because the only leg that could name it said something else.

Two legs, because either alone leaves a hole:

* **behaviour** -- a DISCRIMINATING TRIPLE built in throwaway trees. Cases 1 and 2 carry an
  IDENTICAL stale ledger and differ ONLY in whether a scorecard artifact exists, and they must
  return DIFFERENT states. That pair is the control (AGENTS.md rule 7): if the artifact read
  were dead code, both arms would collapse to the same answer and this leg fails. A constant
  cannot satisfy it. Cases 3 and 4 pin the other two branches (a healthy run stays HELD; no
  ledger at all is a missed run).
* **surface** -- the state must REACH the reader. `render_cadence_label` must give three
  pairwise-distinct labels, and the rendered markdown report must carry the dropped-row label
  AND the record name, driven end-to-end from a fixture through the real
  `check_cadence_integrity` -> `format_report_markdown` path.
* **shared label** (source) -- the report row and the stdout line must CALL the shared label.
  Both sites previously re-derived it inline (`'HELD' if cadence_held else 'MISSED'`), which is
  exactly the pattern that swallowed the distinction; the scan fires on that pattern and on
  nothing else, so it discriminates rather than passing over any source.

Runs as a script (`python3 tests/test_audit_cadence_record.py`, how tools/audit.py invokes it).
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

# The inline re-derivation the fix removed from two surfaces.
CADENCE_REDERIVED = re.compile(r"'HELD' if cadence_")
# The shared label the surfaces must call instead.
LABEL_SITE = "render_cadence_label("
MIN_LABEL_SITES = 2
# Staleness threshold the leg uses is 30h; 40h is unambiguously past it.
STALE_HOURS = 40.0
FRESH_HOURS = 1.0


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def build_fixture(root: Path, *, row_hours_ago: float | None, artifact: bool) -> tuple[Path, Path]:
    """A throwaway `(ledger, scores)` pair. `row_hours_ago=None` writes no ledger at all.

    When `artifact` is set, the scorecard is dated the DAY AFTER the last recorded row --
    which is the semantic under test: a run happened for which no row was ever written.
    """
    ledger = root / "evidence" / "ledger.jsonl"
    scores = root / "evidence" / "scores"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    if row_hours_ago is not None:
        row_dt = _utcnow() - datetime.timedelta(hours=row_hours_ago)
        row = {
            "n": 1,
            "ts": row_dt.isoformat().replace("+00:00", "Z"),
            "event": "run",
            "actor": "hq",
            "detail": "fixture row",
        }
        ledger.write_text(json.dumps(row) + "\n", encoding="utf-8")
        if artifact:
            scores.mkdir(parents=True, exist_ok=True)
            stamp = (row_dt.date() + datetime.timedelta(days=1)).isoformat()
            (scores / f"{stamp}-self-audit.md").write_text("# fixture scorecard\n", encoding="utf-8")
    return ledger, scores


def _cadence(ledger: Path, scores: Path, problems: list[str], case: str) -> dict:
    """`check_cadence_integrity(ledger, scores_dir)` -- or a NAMED problem, never a traceback.

    A pre-fix `audit.py` takes the ledger alone, so the two-argument call raises `TypeError`.
    That IS the red this gate is for, and it must be reported as a violation rather than
    crash the gate: a traceback tells the reader the gate broke, not that the tool lost the
    record it reads (#116).
    """
    try:
        return audit.check_cadence_integrity(ledger, scores)
    except TypeError as exc:
        problems.append(
            f"{case}: check_cadence_integrity(ledger, scores_dir) is not callable with the artifact "
            f"directory ({exc}); the leg cannot read the scorecard series and cannot name a dropped row"
        )
        return {}


def leg_behaviour(problems: list[str]) -> None:
    """The discriminating triple: identical stale ledger, artifact present vs absent."""
    cases = [
        # name,                row_hours_ago, artifact, expected state, expected held
        ("dropped_row", STALE_HOURS, True, "dropped_row", False),
        ("missed_run", STALE_HOURS, False, "missed_run", False),
        ("held", FRESH_HOURS, False, "held", True),
        ("no_ledger", None, False, "missed_run", False),
    ]
    seen: dict[str, str] = {}
    for name, hours, artifact, want_state, want_held in cases:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, scores = build_fixture(Path(tmp), row_hours_ago=hours, artifact=artifact)
            stats = _cadence(ledger, scores, problems, f"behaviour[{name}]")
        if not stats:
            continue
        seen[name] = stats.get("state", "<missing>")
        if stats.get("state") != want_state:
            problems.append(
                f"behaviour[{name}]: expected state {want_state!r}, got {stats.get('state')!r} "
                f"(row_hours_ago={hours}, artifact={artifact})"
            )
        if stats.get("cadence_held") is not want_held:
            problems.append(
                f"behaviour[{name}]: expected cadence_held={want_held}, got {stats.get('cadence_held')!r}"
            )
    # The control: the two stale arms share a ledger and differ ONLY in the artifact.
    if "dropped_row" in seen and "missed_run" in seen and seen["dropped_row"] == seen["missed_run"]:
        problems.append(
            "behaviour[control]: a dropped row and a missed run both reported "
            f"{seen['dropped_row']!r} -- the artifact read is not discriminating (#116)"
        )


def leg_record_named(problems: list[str]) -> None:
    """The leg states WHICH record it reads, in every branch (#116's literal goal)."""
    for name, hours, artifact in [
        ("dropped_row", STALE_HOURS, True),
        ("missed_run", STALE_HOURS, False),
        ("held", FRESH_HOURS, False),
        ("no_ledger", None, False),
    ]:
        with tempfile.TemporaryDirectory() as tmp:
            ledger, scores = build_fixture(Path(tmp), row_hours_ago=hours, artifact=artifact)
            stats = _cadence(ledger, scores, problems, f"record_read[{name}]")
        if not stats:
            continue
        record = stats.get("record_read")
        if not isinstance(record, str) or not record.strip():
            problems.append(f"record_read[{name}]: absent or empty; the leg must name its record")
        elif "ledger" not in record:
            problems.append(f"record_read[{name}]: {record!r} does not name the ledger")


def leg_surface(problems: list[str]) -> None:
    """The state reaches the reader: distinct labels, and the report carries it."""
    render_label = getattr(audit, "render_cadence_label", None)
    if render_label is None:
        problems.append(
            "surface: tools/audit.py defines no render_cadence_label, so no surface can render the "
            "dropped-row state distinctly and the label is re-derived per site (#116)"
        )
        return
    labels = {state: render_label({"state": state}) for state in ("held", "missed_run", "dropped_row")}
    if len(set(labels.values())) != 3:
        problems.append(f"surface: the three states do not render distinctly: {labels}")
    if "DROPPED ROW" not in labels["dropped_row"]:
        problems.append(f"surface: the dropped-row label reads {labels['dropped_row']!r}, which does not name it")
    if "MISSED" in labels["dropped_row"]:
        problems.append("surface: the dropped-row label still contains MISSED, the reading this gate exists to remove")

    # End-to-end: fixture -> the real function -> the rendered report row.
    with tempfile.TemporaryDirectory() as tmp:
        ledger, scores = build_fixture(Path(tmp), row_hours_ago=STALE_HOURS, artifact=True)
        stats = _cadence(ledger, scores, problems, "surface[end-to-end]")
    if not stats:
        return
    md = audit.format_report_markdown("2026-10-05", {}, {}, stats, [])
    rows = [ln for ln in md.splitlines() if "Cadence Status" in ln]
    if not rows:
        problems.append("surface: the report carries no Cadence Status row")
    else:
        row = rows[0]
        if "DROPPED ROW" not in row:
            problems.append(f"surface: the report row does not name the dropped row: {row.strip()}")
        if stats["record_read"] not in row:
            problems.append(f"surface: the report row does not state the record it read: {row.strip()}")


def leg_shared_label(problems: list[str]) -> None:
    """The surfaces CALL the shared label rather than re-deriving it inline."""
    src = (REPO / "tools" / "audit.py").read_text(encoding="utf-8")
    hits = sorted(set(CADENCE_REDERIVED.findall(src)))
    if hits:
        problems.append(
            f"tools/audit.py re-derives the cadence label inline ({', '.join(hits)}); a surface "
            "must call render_cadence_label, or the dropped-row distinction is dropped there (#116)"
        )
    sites = src.count(LABEL_SITE)
    if sites < MIN_LABEL_SITES:
        problems.append(
            f"tools/audit.py calls {LABEL_SITE} {sites} time(s); the report row and the stdout "
            f"line must both use it (>= {MIN_LABEL_SITES})"
        )


def main() -> int:
    problems: list[str] = []
    leg_behaviour(problems)
    leg_record_named(problems)
    leg_surface(problems)
    leg_shared_label(problems)

    print("Gate: cadence leg names its record and separates a dropped row from a missed run (#116)")
    print("  predicate: check_cadence_integrity reads ledger rows AND the scorecard artifact series;")
    print("             state in {held, missed_run, dropped_row}; every surface renders the shared label")
    if problems:
        print(f"FAIL: {len(problems)} violation(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("PASS: 4 fixture branches, 3 distinct labels, report row carries the state and the record")
    return 0


if __name__ == "__main__":
    sys.exit(main())
