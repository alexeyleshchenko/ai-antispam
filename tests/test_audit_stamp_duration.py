#!/usr/bin/env python3
"""Gate: the self-audit stamp's `duration=` is MEASURED, and it tracks the work it describes.

Why this file exists
--------------------
`tools/audit.py` wrote the daily `internal-self-audit` run row's duration as the STRING
LITERAL `4s`. All 15 such rows in `evidence/ledger.jsonl` therefore carried the same
duration -- constant, plausible and wrong -- while the run they describe takes minutes
(measured 2026-09-29: `tests/test_ledger.py` 212.73s, `tests/test_cron_thinness.py` 70.17s).
A reader comparing runs, or a patrol asking whether the audit is getting slower as gates are
added, read a constant and concluded the cost was stable. Issue #71.

Two legs, because either one alone leaves the hole its sibling gates describe:

* **source** -- the stamp site INTERPOLATES the value. A numeric literal there IS the
  defect, and the scan is exact: it fires on the very literal the fix removed, so it
  discriminates rather than passing over any source.
* **behaviour** -- two runs in throwaway trees whose staged gate work DIFFERS (1s and 5s).
  Each row's duration must sit at or above the gate table that SAME run printed, and the
  slower run's row must be the LARGER of the two. This is the close condition's leg 2 made
  non-vacuous: as written ("not equal to the previous run's") it passes a re-introduced
  literal whose value happens to differ, and fails spuriously when two runs share a wall
  time. No constant satisfies BOTH a moving lower bound and an ordering -- one below the
  work fails the bound, one above it fails the ordering.

The positive control is the staged gate's OWN printed duration: if the loop had not really
slept, the bound would be asserted against a number nothing produced (AGENTS.md rule 7).

Runs two ways, matching the sibling gates: as a script (`python3
tests/test_audit_stamp_duration.py`, which is how tools/audit.py invokes it) and under
pytest, which collects the `test_*` wrappers.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# A duration DECLARED as a literal number at the stamp site: `duration=4s`, `duration=4.0s`.
STAMP_LITERAL = re.compile(r"duration=\d+(?:\.\d+)?s")
# One line of the printed gate table: `  [PASS] python3 tests/x.py (5.02s)`.
GATE_ROW = re.compile(r"\[(?:PASS|FAIL)\] .*?\((\d+(?:\.\d+)?)s\)")
# The stamped row's own declaration.
ROW_DURATION = re.compile(r"duration=(\d+(?:\.\d+)?)s")
# Both the per-gate values and the row's are rounded to 2 dp, so the row can sit below the
# printed SUM by at most (N+1) x 0.005s. 0.25s covers a handful of gates and stays far
# below the 4s of staged work that separates the two runs.
SUM_TOLERANCE_SEC = 0.25
# The staged gate's printed duration must reach this fraction of the sleep it was given,
# or the loop demonstrably did not do the work the bound is asserted against.
CONTROL_FLOOR = 0.8


def check_source_carries_no_literal() -> list[str]:
    """The stamp site interpolates the duration; no numeric literal survives there."""
    problems: list[str] = []
    src = (REPO / "tools" / "audit.py").read_text(encoding="utf-8")
    hits = sorted(set(STAMP_LITERAL.findall(src)))
    if hits:
        problems.append(
            f"tools/audit.py declares a hardcoded duration literal ({', '.join(hits)}); "
            "the stamp row must carry wall-clock elapsed, never a typed constant (#71)"
        )
    # The guard must be able to FAIL: the expression the fix replaced is caught by this
    # same scan, so a regression is detectable rather than merely unobserved.
    if not STAMP_LITERAL.search('detail = f"duration=4s turns=0 outcome={outcome}"'):
        problems.append(
            "the pre-fix literal is NOT caught by this scan -- the check cannot detect a "
            "regression and is decorative"
        )
    return problems


def _stage_tree(tree: Path, sleep_sec: float) -> None:
    """Build a throwaway tree whose ONE registered gate deliberately sleeps."""
    (tree / "tools").mkdir(parents=True)
    (tree / "tests").mkdir()
    (tree / "evidence").mkdir()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gate_fixtures import stage_tool

    # stage_tool computes the import closure, so nothing here hardcodes which modules
    # ledger.py or audit.py import and the assumption cannot go stale (P35).
    stage_tool(REPO / "tools" / "audit.py", tree / "tools", REPO / "tools")
    stage_tool(REPO / "tools" / "ledger.py", tree / "tools", REPO / "tools")
    # audit.py registers `tests/test_rework.py` as a gate when the file is present, so
    # this is the tree's only gate -- and it is SLOW by construction, because the loop's
    # wall must exceed any plausible literal for the lower bound to have teeth.
    (tree / "tests" / "test_rework.py").write_text(
        "import sys, time\n"
        f"time.sleep({sleep_sec})\n"
        f"print('staged probe gate slept {sleep_sec}s')\n"
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    (tree / "evidence" / "ledger.jsonl").write_text("", encoding="utf-8")


def _stamp_run(base: Path, sleep_sec: float) -> dict[str, object]:
    """Stage, stamp, and read back the row's duration and the printed gate table."""
    tree = base / "repo"
    _stage_tree(tree, sleep_sec)
    res = subprocess.run(
        [sys.executable, "tools/audit.py", "--stamp"],
        cwd=tree,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            # The migrated tool resolves an --actor through the fleet registry unless the
            # append is marked a FIXTURE, and the mark is OC_LEDGER_PATH being set. It is
            # pinned to the SAME file the tree already uses, so the seam is honoured without
            # moving the ledger out from under tools/audit.py, which resolves
            # evidence/ledger.jsonl from its own REPO_ROOT and honours no override.
            "OC_LEDGER_PATH": str(tree / "evidence" / "ledger.jsonl"),
            "OC_ACTORS_PATH": str(tree / "evidence" / "actors-fixture.txt"),
        },
    )

    out: dict[str, object] = {"sleep": sleep_sec, "stdout": res.stdout, "stderr": res.stderr}

    printed: list[float] = []
    staged: list[float] = []
    for line in res.stdout.splitlines():
        m = GATE_ROW.search(line)
        if not m:
            continue
        printed.append(float(m.group(1)))
        if "test_rework.py" in line:
            staged.append(float(m.group(1)))
    out["printed"] = printed
    out["staged"] = staged
    out["sum"] = sum(printed)

    ledger = tree / "evidence" / "ledger.jsonl"
    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    runs = [
        r for r in rows
        if r.get("event") == "run" and r.get("subject") == "internal-self-audit"
    ]
    if not runs:
        out["row"] = None
        return out
    detail = runs[-1].get("detail", "")
    declared = ROW_DURATION.findall(detail)
    out["declared_count"] = len(declared)
    out["detail"] = detail
    out["row"] = float(declared[-1]) if declared else None
    return out


def check_measured_and_tracks_work(base: Path) -> list[str]:
    """A stamped row's duration is measured, and it moves with the work the run did."""
    problems: list[str] = []
    runs = [_stamp_run(base / "fast", 1.0), _stamp_run(base / "slow", 5.0)]

    for r in runs:
        label = f"sleep={r['sleep']}s tree"
        if not r["printed"]:
            problems.append(
                f"{label}: the run printed no gate table -- nothing was measured "
                f"(rc-independent). stdout tail: {str(r['stdout'])[-400:]}"
            )
            continue
        # POSITIVE CONTROL: the staged gate really did the work, so the bound below is
        # asserted against a number the run produced rather than against nothing.
        if not r["staged"] or r["staged"][0] < CONTROL_FLOOR * float(r["sleep"]):
            problems.append(
                f"{label}: the staged gate printed {r['staged']}s for a {r['sleep']}s sleep "
                f"-- the loop did not do the work this check bounds, so the instrument "
                f"cannot observe the failure class it is aimed at"
            )
        if r["row"] is None:
            problems.append(f"{label}: no internal-self-audit run row was stamped")
            continue
        if r.get("declared_count") != 1:
            problems.append(
                f"{label}: the row declares duration= {r.get('declared_count')} times; one "
                f"declaration is the canonical reading. detail: {r.get('detail')}"
            )
        row = float(r["row"])
        if row < float(r["sum"]) - SUM_TOLERANCE_SEC:
            problems.append(
                f"{label}: the row says duration={row}s while the gate table it printed in "
                f"the SAME run sums to {round(float(r['sum']), 2)}s -- the field does not "
                f"describe the work the run did"
            )

    if problems:
        return problems

    fast, slow = runs
    if float(slow["row"]) <= float(fast["row"]):
        problems.append(
            f"the row duration does not track the work: {fast['sleep']}s of gate work "
            f"stamped {fast['row']}s and {slow['sleep']}s stamped {slow['row']}s -- a "
            f"constant satisfies both, which is the defect #71 removed"
        )
    return problems


# NO PYTEST WRAPPERS, DELIBERATELY (direction 5, tests/test_gate_registration.py). A file
# carrying module-level `def test_*` is PYTEST-form HOWEVER it is invoked (#124, probed at
# `:754`), so a script registration would leave those wrappers uncollected and the
# registration a direction-5 mismatch -- and the pytest form is the expensive one here:
# ~46s against this file's ~10s script run, because a pytest invocation drags in
# tests/conftest.py and `pythonpath = src`. The two legs above are the whole guard, so a
# wrapper could add nothing but the mismatch.


# --- standalone entry point (how tools/audit.py runs it) --------------------


def main() -> int:
    problems = check_source_carries_no_literal()
    with tempfile.TemporaryDirectory(prefix="audit-stamp-duration-") as td:
        problems += check_measured_and_tracks_work(Path(td))

    if problems:
        print(f"audit-stamp duration guard FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  {p}")
        return 1

    print(
        "audit-stamp duration guard clean: the stamp site carries no literal, and the row "
        "tracks the gate work it describes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
