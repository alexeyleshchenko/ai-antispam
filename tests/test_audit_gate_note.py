"""Guard: the scorecard's gate note carries the gate's POPULATION, not one row.

Why this file exists
--------------------
`tools/audit.py` built each gate's table note from the gate's LAST stdout line,
truncated to 60 chars. A failing gate prints a header carrying its population
("N problem(s)") and then ONE LINE PER PROBLEM, so the last line is only the
final item:

    ledger schema violations (3 problem(s) in .../ledger.jsonl):   <- header
      line 15: unauthorized actor 'worker' for event 'intake'     <- item 1
      line 26: ...                                                <- item 2
      line 30: ...                                                <- item 3 (LAST)

Measured 2026-09-25: the schema gate found THREE violations and the scorecard
named one of them. A triager who repaired the named row would believe the gate
done -- the same class as a number travelling without its population.

The fix takes the FIRST non-empty line on FAIL (the header, which carries the
count) and keeps the LAST line on PASS (a passing gate prints one summary
line), and appends every failing gate's FULL output to section 2.1 so the
population is readable without re-running the gate.

Runs two ways, matching the sibling gates: as a script (`python3
tests/test_audit_gate_note.py`, which is how tools/audit.py invokes it and how
the daily pacemaker therefore reaches it) and under pytest, which collects the
`test_*` wrappers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from audit import format_report_markdown, gate_note  # noqa: E402

GATE_FAIL_3 = {
    "cmd": "python3 tests/test_ledger_schema.py",
    "exit_code": 1,
    "passed": False,
    "duration_sec": 0.21,
    "stdout": (
        "ledger schema violations (3 problem(s) in /repo/evidence/ledger.jsonl):\n"
        "  line 15: unauthorized actor 'worker' for event 'intake'\n"
        "  line 26: unauthorized actor 'worker' for event 'intake'\n"
        "  line 30: unauthorized actor 'worker' for event 'intake'\n"
    ),
    "stderr": "",
}

GATE_PASS = {
    "cmd": "python3 tools/ledger.py verify",
    "exit_code": 0,
    "passed": True,
    "duration_sec": 0.05,
    "stdout": "ledger clean: 32 row(s), monotonic, all event types known, sequences complete\n",
    "stderr": "",
}


def _render(gates: list[dict[str, Any]]) -> str:
    return format_report_markdown(
        "2026-09-25",
        {"total_events": 32, "closed_tasks": 5, "run_events": 12, "runs_by_outcome": {}},
        {"total_entries": 3, "rework_rate": 0.75},
        {"cadence_held": True, "hours_since_last_run": 24.0},
        gates,
    )


def _gate_row(body: str, cmd: str) -> str:
    for line in body.splitlines():
        if line.startswith("| `") and cmd in line:
            return line
    return ""


def check_fail_note_carries_population() -> list[str]:
    """A failing gate's note names its POPULATION, not its final item."""
    problems: list[str] = []
    body = _render([GATE_FAIL_3])
    row = _gate_row(body, "tests/test_ledger_schema.py")
    if not row:
        return ["the failing gate has no row in the gate table"]
    if "3 problem(s)" not in row:
        problems.append(
            f"the gate row does not state the population the gate reported "
            f"(3 problem(s)); row reads: {row}"
        )
    return problems


def check_prefix_expression_disagrees() -> list[str]:
    """The guard must be able to FAIL: the expression this fix replaced reads a
    different note at a 3-problem gate, so the check above discriminates."""
    problems: list[str] = []
    old_note = GATE_FAIL_3["stdout"].splitlines()[-1].strip().replace("|", "/")[:60]
    if "3 problem(s)" in old_note:
        problems.append(
            "the pre-fix expression already carries the population at this gate "
            "-- this check no longer discriminates and cannot detect a regression"
        )
    if gate_note(GATE_FAIL_3) == old_note:
        problems.append(
            f"gate_note() agrees with the pre-fix expression ({old_note!r}); the "
            f"population is not being read"
        )
    return problems


def check_pass_note_unchanged() -> list[str]:
    """A passing gate keeps its summary line -- the fix must not disturb PASS."""
    problems: list[str] = []
    body = _render([GATE_PASS])
    row = _gate_row(body, "tools/ledger.py verify")
    if "ledger clean: 32 row(s)" not in row:
        problems.append(f"a passing gate lost its summary line; row reads: {row}")
    if "### 2.1" in body:
        problems.append("section 2.1 was emitted for a run with no failing gate")
    return problems


def check_failing_output_is_complete() -> list[str]:
    """Every failing gate's FULL output lands in section 2.1, so the population
    is readable from the artifact without re-running the gate."""
    problems: list[str] = []
    body = _render([GATE_PASS, GATE_FAIL_3])
    if "### 2.1 Failing gate output (full)" not in body:
        return ["a run with a failing gate emitted no section 2.1"]
    for needle in ("line 15:", "line 26:", "line 30:"):
        if needle not in body:
            problems.append(f"section 2.1 omits {needle!r} -- the population is truncated")
    if "`tools/ledger.py verify`** -- rc=0" in body:
        problems.append("a PASSING gate was listed in section 2.1")
    return problems


# --- pytest wrappers (the gate path does not need pytest) -------------------

def test_fail_note_carries_population() -> None:
    assert check_fail_note_carries_population() == []


def test_prefix_expression_disagrees() -> None:
    assert check_prefix_expression_disagrees() == []


def test_pass_note_unchanged() -> None:
    assert check_pass_note_unchanged() == []


def test_failing_output_is_complete() -> None:
    assert check_failing_output_is_complete() == []


# --- standalone entry point (how tools/audit.py runs it) --------------------

def main() -> int:
    problems = (
        check_fail_note_carries_population()
        + check_prefix_expression_disagrees()
        + check_pass_note_unchanged()
        + check_failing_output_is_complete()
    )
    if problems:
        print(f"gate-note guard FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  {p}")
        return 1
    print("gate-note guard clean: a FAIL note carries its population, PASS keeps its summary")
    return 0


if __name__ == "__main__":
    sys.exit(main())
