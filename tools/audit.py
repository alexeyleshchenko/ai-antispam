#!/usr/bin/env python3
"""Operational Process Audit & Self-Audit Tool.

Upholds Process 3 (Internal Self-Audit / Operational Measurement).
Reads the evidence chain (ledger.jsonl, rework.md), computes operational
metrics (yield, rework rate, cadence, lead time), runs mechanical gates,
and optionally stamps a telemetry run row or generates a scored report.

Usage:
  python3 tools/audit.py              # Run check & print metrics/gates
  python3 tools/audit.py --json       # Output machine-readable JSON
  python3 tools/audit.py --report     # Generate dated evidence/scores/<date>.md
  python3 tools/audit.py --stamp      # Record run event in evidence/ledger.jsonl
  python3 tools/audit.py --report --stamp  # One pass: the run row is written BEFORE the
                                           # report, so the scorecard includes its own run
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Sibling modules, on the APPEND convention -- never `insert(0)`. `tools/registry.py`
# shares its name with the repo's own `registry/` package, so putting this directory
# FIRST on the path would let the module beside this file shadow the repo's declaration
# surface. Appending resolves both call shapes this file must serve -- run as a script
# (its own dir is already sys.path[0]) and imported by `tests/` (which appends `tools/`)
# -- without reordering anything.
sys.path.append(str(Path(__file__).resolve().parent))
from field_predicate import declared_cause_count  # noqa: E402
from gate_budget import TIMEOUT_EXIT_CODE  # noqa: E402


def first_pass_yield_pct(accepted: int, total: int) -> float:
    """The ONE site where a yield becomes a percentage (#38).

    Both artifacts of a run read this function: the ledger row's `yield=` field
    and the scorecard's First-Pass Yield. Rounding in two places is how they came
    to state two different numbers for one run, so there is exactly one expression
    here and every display site calls it.
    """
    return round(accepted / total * 100, 1) if total else 0.0


def hygiene_namespace(repo_root: Path) -> str:
    """The scratch namespace this factory OWNS, resolved from ANY worktree (#174).

    `tools/hygiene.py` derives its own namespace from the directory the tool
    sits in, which is right only in the MAIN worktree: run from a linked
    worktree it globs `/tmp/<worktree-dir>-*`, a namespace belonging to nobody,
    and returns a clean verdict over a population it never examined. The
    canonical namespace is the main worktree's directory name, and
    `git rev-parse --git-common-dir` resolves it from either. A tree that is not
    a checkout at all falls back to its own directory name, which is the
    pre-#174 behaviour and the correct one there.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return repo_root.name
    if proc.returncode != 0 or not proc.stdout.strip():
        return repo_root.name
    git_dir = Path(proc.stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    return git_dir.resolve().parent.name


def parse_ledger(ledger_path: Path) -> dict[str, Any]:
    """Parse ledger.jsonl and calculate operational delivery metrics, including token/cost economics."""
    if not ledger_path.is_file():
        return {
            "exists": False,
            "total_events": 0,
            "event_counts": {},
            "closed_tasks": 0,
            "intake_tasks": 0,
            "run_events": 0,
            "runs_by_outcome": {},
            "first_pass_yield": 1.0,
            "lead_times_sec": [],
            "avg_lead_time_sec": 0.0,
            "total_cost_usd": 0.0,
            "avg_cost_per_closed_task_usd": 0.0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_turns": 0,
            "latest_closed_subject": None,
            "spot_check": None,
        }

    events: list[dict[str, Any]] = []
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass

    event_counts: dict[str, int] = {}
    subjects_intake: dict[str, str] = {}
    subjects_claim: dict[str, str] = {}
    subjects_close: dict[str, str] = {}
    lead_times_sec: list[float] = []

    runs_by_outcome: dict[str, int] = {
        "accepted": 0,
        "failed": 0,
        "reworked": 0,
        "abandoned": 0,
    }
    total_runs = 0
    total_cost_usd = 0.0
    total_tokens_in = 0
    total_tokens_out = 0
    total_turns = 0

    for ev in events:
        ev_type = ev.get("event", "unknown")
        event_counts[ev_type] = event_counts.get(ev_type, 0) + 1
        subj = ev.get("subject", "")
        ts = ev.get("ts", "")
        detail = ev.get("detail", "")

        # Parse key-value metadata from detail
        for part in detail.split():
            if "=" in part:
                k, v = part.split("=", 1)
                if k in ("cost_usd", "cost"):
                    try:
                        total_cost_usd += float(v.rstrip("$"))
                    except ValueError:
                        pass
                elif k in ("tokens_in", "in_tokens"):
                    try:
                        total_tokens_in += int(v)
                    except ValueError:
                        pass
                elif k in ("tokens_out", "out_tokens"):
                    try:
                        total_tokens_out += int(v)
                    except ValueError:
                        pass
                elif k == "turns":
                    try:
                        total_turns += int(v)
                    except ValueError:
                        pass

        if ev_type == "intake" and subj and subj not in subjects_intake:
            subjects_intake[subj] = ts
        elif ev_type == "claim" and subj and subj not in subjects_claim:
            subjects_claim[subj] = ts
        elif ev_type == "close" and subj:
            subjects_close[subj] = ts
            if subj in subjects_intake:
                try:
                    t_in = datetime.datetime.fromisoformat(subjects_intake[subj].replace("Z", "+00:00"))
                    t_cl = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    diff = (t_cl - t_in).total_seconds()
                    if diff >= 0:
                        lead_times_sec.append(diff)
                except Exception:
                    pass

        elif ev_type == "run":
            total_runs += 1
            outcome = "accepted"
            for part in detail.split():
                if part.startswith("outcome="):
                    outcome = part.split("=", 1)[1]
                    break
            runs_by_outcome[outcome] = runs_by_outcome.get(outcome, 0) + 1

    # First-pass yield: accepted runs / total runs
    if total_runs > 0:
        yield_val = runs_by_outcome.get("accepted", 0) / total_runs
    else:
        yield_val = 1.0

    avg_lead_time = (sum(lead_times_sec) / len(lead_times_sec)) if lead_times_sec else 0.0
    closed_count = len(subjects_close)
    avg_cost_per_closed_task = (total_cost_usd / closed_count) if closed_count > 0 else 0.0

    # Spot check the latest closed subject
    latest_closed = None
    spot_check = None
    for ev in reversed(events):
        if ev.get("event") == "close":
            latest_closed = ev.get("subject")
            break

    if latest_closed:
        has_in = latest_closed in subjects_intake
        has_clm = latest_closed in subjects_claim
        spot_check = {
            "subject": latest_closed,
            "intake_verified": has_in,
            "claim_verified": has_clm,
            "close_verified": True,
            "sequence_intact": has_in and has_clm,
        }

    return {
        "exists": True,
        "total_events": len(events),
        "event_counts": event_counts,
        "closed_tasks": closed_count,
        "intake_tasks": len(subjects_intake),
        "run_events": total_runs,
        "runs_by_outcome": runs_by_outcome,
        # The RAW ratio, deliberately NOT pre-rounded (#38). Percentages are produced at
        # ONE site -- first_pass_yield_pct() -- which both the run row and the scorecard
        # read, so one run cannot state two yields.
        #
        # Measured against the code this replaced (parser pre-rounded to 4 dp; the row did
        # int(v * 100); the report did round(v * 100, 1)) over all a/d for denominators
        # 1-399, 80,199 pairs. The two counts answer DIFFERENT questions -- keep them apart:
        #   row-vs-report divergence in VALUE ... 74,451 pairs, first at 1/3 (row 33 vs report 33.3)
        #   report value changed by the fix ..... 3,611 pairs, first at 14/17 (82.3 -> 82.4)
        #   divergence as RENDERED strings ....... 80,199 pairs (row "50%" vs report "50.0%")
        "first_pass_yield": yield_val,
        "lead_times_sec": lead_times_sec,
        "avg_lead_time_sec": round(avg_lead_time, 1),
        "total_cost_usd": round(total_cost_usd, 4),
        "avg_cost_per_closed_task_usd": round(avg_cost_per_closed_task, 4),
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_turns": total_turns,
        "latest_closed_subject": latest_closed,
        "spot_check": spot_check,
    }


def parse_rework(rework_path: Path, closed_tasks: int) -> dict[str, Any]:
    """Parse rework.md to extract defects, unprevented items, and rework rate."""
    if not rework_path.is_file():
        return {
            "exists": False,
            "total_entries": 0,
            "unprevented_entries": 0,
            "rework_rate": 0.0,
        }

    entries = 0
    unprevented = 0
    with open(rework_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("|") and not line.startswith("| Column") and not line.startswith("|---"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 7 and parts[1] and not parts[1].startswith("Date") and not parts[1].startswith("*"):
                    entries += 1
                    prevented_col = parts[6].lower()
                    if "nothing yet" in prevented_col or not prevented_col:
                        unprevented += 1

    rework_rate = (entries / max(1, closed_tasks)) if closed_tasks > 0 else 0.0

    return {
        "exists": True,
        "total_entries": entries,
        "unprevented_entries": unprevented,
        "rework_rate": round(rework_rate, 4),
    }


# Each gate's wall, as a DECLARED MULTIPLE of a MEASURED runtime. A gate with no
# entry uses the default and the audit PRINTS that it did, so a fallback is never
# an exempt-by-silence surface (ai-antispam#64).
DEFAULT_GATE_WALL_SEC = 30.0
GATE_WALLS_SEC: dict[str, float] = {
    # measured 40 / 60 / 87s wall over three consecutive runs on 2026-09-29 (its own
    # work is a few seconds; the rest is interpreter + plugin startup plus the
    # worktree-fork probe's two driven checkouts, which are load-sensitive). 100.0 was
    # a 1.15x factor on the SLOWEST of those -- not a margin -- and the gate duly
    # reported a 100.12s "failure" that was a TIMEOUT, hiding a passing gate behind a
    # red audit. 350.0 is 4.0x the slowest measurement, the house precedent.
    "tests/test_ledger.py": 350.0,
    # RE-DERIVED 2026-10-02 under #72's q10 ruling ("calibrate for co-tenant load"), and the
    # re-derivation trigger is the manifest's own law: a sample EXHAUSTED the budget, so the
    # audit reported UNKNOWN for a gate that passes. The previous 70.0 came from three
    # UNLOADED runs, and the box the audit actually runs on is shared. REGIME: co-tenant LOADED.
    #   measured_sec    30.08   <- WORST OBSERVED of 12 whole-command samples, which is the
    #                             basis the law names ("a budget must contain the slow tail,
    #                             not the median"; median 22.73, mean 22.19, stdev 3.79)
    #   load_at_measure  7.87   <- 1-min load on 4 cores at that worst sample (range 4.51-7.87)
    #   margin_x       4.0249   <- 4.0 + 0.75/measured_sec, the manifest's stated margin law
    #   budget_sec     121.07   <- margin_x x measured_sec
    # 70.0 was a 1.73x factor on this loaded worst case -- not a margin -- so the gate was
    # killed mid-pass and its timeout was rendered as a gate FAILURE. The basis is a MEASURED
    # runtime, NEVER the 70.17s the kill itself recorded: a killed gate's duration is a LOWER
    # BOUND on the cap that stopped it, and promoting it to a basis derives the next cap from
    # the last one (#226). The 4x margin is what absorbs a heavier co-tenant day than sampled.
    "tests/test_cron_thinness.py": 121.07,
    # measured 0.66 / 0.33 / 0.31s wall over three consecutive runs (script-mode, stdlib
    # only, so the cost is interpreter startup). 5.0 is a 7.6x factor on the SLOWEST run
    # -- a margin rather than a coincidence, and still far below the 30.0 default.
    "tests/test_ledger_commit_cites_no_rows.py": 5.0,
    # measured 9.97 / 9.55 / 10.23 / 13.14 / 9.67 / 11.45 / 12.71 / 11.51 / 12.83s wall over
    # nine runs on 2026-09-30-10-01, the later ones taken at load 11.49 on 4 cores (co-tenant
    # load, board item #72). The cost is NOT startup alone: ~6.0s is the guard's own
    # deliberate sleeps (a 1.0s and a 5.0s staged gate, load-insensitive by construction),
    # the rest is interpreter startup plus two driven `audit.py --stamp` subprocesses. 53.0
    # is 4.0x the slowest of those nine -- the house precedent. The spread is load, not the
    # guard: the fixed sleeps put a floor under the runtime and a ceiling on how far
    # contention can stretch it.
    "tests/test_audit_stamp_duration.py": 53.0,
}


def _gate_wall_key(cmd: list[str]) -> str:
    """The command element a gate's wall is keyed on.

    Was `cmd[-1]` until the pytest-form registrations landed: a command of the shape
    `[python, -m, pytest, tests/x.py, -q]` ends in `-q`, so the lookup silently took
    the DEPTH default and the gate's own declared wall never applied. Measured on
    registering the cron gate: the audit printed "gate budget: -q uses the declared
    default 30.0s", ran the gate under 30s, and reported a timeout at 30.19s for a
    gate that completes in ~16s. A wall that never applies is not a budget.
    """
    for part in cmd:
        if part in GATE_WALLS_SEC:
            return part
    for part in cmd:
        if part.endswith(".py"):
            return part
    return cmd[-1]


def _gate_wall(cmd: list[str]) -> float:
    """The declared wall for a gate, keyed on the file it runs (never a flag)."""
    return GATE_WALLS_SEC.get(_gate_wall_key(cmd), DEFAULT_GATE_WALL_SEC)


def run_gate(cmd: list[str], cwd: Path, budget_sec: float = DEFAULT_GATE_WALL_SEC) -> dict[str, Any]:
    """Run an individual verification gate under its DECLARED time budget.

    `budget_sec` is the wall for THIS gate, and it is a DECLARED MULTIPLE of a
    MEASURED runtime rather than a round number: a wall picked by feel kills a
    healthy gate when too low and hides a hung one when too high (ai-antispam#64, #72).

    A gate that exceeds its wall is UNKNOWN -- it neither passed nor failed, and
    reporting it as either would be a verdict nobody measured. So the timeout branch
    carries the DISTINCT `TIMEOUT_EXIT_CODE` (never the generic 99 a crash gets), the
    `unknown` flag, and the MEASURED elapsed time; the crash branch keeps 99 and
    `unknown: False`. Before #72 both branches returned 99 with no `unknown` key, so a
    timeout was indistinguishable from an instant logic error -- the code contradicting
    its own docstring, and the defect this branch now closes.

    ONE BOUNDED RETRY, and ONLY for a timeout (#74 leg 7, kit #93). A load-dependent
    timeout is transient by nature, so a single retry distinguishes a flake from a gate
    that is genuinely too slow; a SECOND timeout is UNKNOWN. The retry is bounded to
    exactly one and its occurrence is RECORDED (`attempts`, `retried`,
    `first_attempt_sec`), because an unrecorded retry becomes a way to hide a gate that
    is genuinely too slow. A genuine FAIL (a non-zero exit) is a VERDICT and is never
    retried; only a timeout, which is no verdict at all, is.
    """
    attempts = 0
    first_attempt_sec: float | None = None
    while True:
        attempts += 1
        t0 = datetime.datetime.now()
        try:
            res = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=budget_sec,
            )
            t_el = (datetime.datetime.now() - t0).total_seconds()
            return {
                "cmd": " ".join(cmd),
                "exit_code": res.returncode,
                "passed": res.returncode == 0,
                "unknown": False,
                "duration_sec": round(t_el, 2),
                "duration_is_lower_bound": False,
                "budget_sec": budget_sec,
                "attempts": attempts,
                "retried": attempts > 1,
                "stdout": res.stdout.strip(),
                "stderr": res.stderr.strip(),
            }
        except subprocess.TimeoutExpired:
            t_el = (datetime.datetime.now() - t0).total_seconds()
            if attempts == 1:
                first_attempt_sec = round(t_el, 2)
                continue
            return {
                "cmd": " ".join(cmd),
                "exit_code": TIMEOUT_EXIT_CODE,
                "passed": False,
                "unknown": True,
                "duration_sec": round(t_el, 2),
                # A KILLED gate's wall-clock is a LOWER BOUND, never a measurement: the gate
                # ran AT LEAST this long and was stopped at the cap, so the number is the
                # budget talking. Stated in the record so no reader -- and no future tool
                # deriving a `measured_sec` from an audit payload -- can promote the cap to a
                # base and derive the next cap from itself (#226).
                "duration_is_lower_bound": True,
                "budget_sec": budget_sec,
                "attempts": attempts,
                "retried": True,
                "first_attempt_sec": first_attempt_sec,
                "stdout": "",
                "stderr": (
                    f"budget exhausted: the gate was killed at {budget_sec:.2f}s after "
                    f"running {t_el:.2f}s, and killed AGAIN on the one bounded retry "
                    f"(first attempt {first_attempt_sec}s) -- UNKNOWN, no verdict taken"
                ),
            }
        except Exception as e:
            t_el = (datetime.datetime.now() - t0).total_seconds()
            detail = f"{type(e).__name__}: {e}".strip().rstrip(":")
            return {
                "cmd": " ".join(cmd),
                "exit_code": 99,
                "passed": False,
                "unknown": False,
                "duration_sec": round(t_el, 2),
                "duration_is_lower_bound": False,
                "budget_sec": budget_sec,
                "attempts": attempts,
                "retried": attempts > 1,
                "stdout": "",
                "stderr": detail,
            }


# One bound for a failing gate's recorded cause, shared by every surface that prints it.
# Raised from the 60 the markdown cell used, because a SCRIPT-shaped failure states a COUNT
# and a SPECIMEN and 60 characters cannot hold both (#74 leg 7 / kit #158). The bound is
# KEPT: this lands on one line of a table whose job is to let the reader decide whether to
# RE-RUN, not to reproduce the log.
GATE_CAUSE_LIMIT = 140

_COUNT_FIRST_RE = re.compile(r"\b\d+\s+(?:problem|violation)\(s\)", re.IGNORECASE)
"""The FALLBACK count shape, reached only when a gate declares no marker (kit #205).

Two nouns, and they are a GUESS AT ENGLISH rather than a property. So the primary reading
is the DECLARED marker (`field_predicate.declared_cause_count`) and this is the fallback,
kept unchanged so a tool that has not adopted the marker behaves as it did before.
"""

def last_reported_line(text: str, limit: int = 200) -> str:
    """The last non-blank line of a gate's output, whitespace-collapsed and BOUNDED.

    The LAST line, because a failing gate states its reason at the end of what it prints --
    pytest writes its failure list there, and a killed gate writes its reason there too.
    BOUNDED, because this lands on one line of the headline report whose job is to let the
    reader decide whether to RE-RUN, not to reproduce the log.
    """
    lines = [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    return lines[-1][:limit]

def reported_cause(text: str, limit: int = GATE_CAUSE_LIMIT) -> str:
    """A failing gate's cause: its COUNT, AND its last line.

    The audit faces TWO output shapes and they put the summary in DIFFERENT places. A
    PYTEST-shaped gate writes its failure list LAST and carries no count line, so the last
    line IS the reason. A SCRIPT-shaped gate prints `N problem(s) in <path>` FIRST and then
    N violations, so the last line is one ARBITRARY specimen and the total is lost.

    PRECEDENCE (kit #205): the tool's own DECLARED marker is read FIRST, and the noun
    heuristic is the FALLBACK. A declaration cannot be moved by phrasing; a noun list is a
    guess at English. So the count line is recorded FIRST when one exists, and the last line
    is appended as the specimen. Both share ONE bound, and the join keeps the HEAD, so the
    count cannot be crowded out by a specimen longer than the remaining budget.
    """
    lines = [" ".join(ln.split()) for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    last = lines[-1]
    counted = next((ln for ln in lines if declared_cause_count(ln) is not None), None)
    if counted is None:
        counted = next((ln for ln in lines if _COUNT_FIRST_RE.search(ln)), None)
    if counted is None or counted == last:
        return last[:limit]
    return f"{counted} \u2014 {last}"[:limit]

def attach_gate_causes(gate_results: list[dict[str, Any]]) -> None:
    """Set `note` on every gate that did not pass, from the ONE predicate above.

    Computed once and read by every surface (the JSON payload, the markdown report and the
    stdout headline), so they cannot disagree about what the gate said. A PASSING gate
    carries no note; an UNKNOWN gate is not a pass and does carry one.
    """
    for g in gate_results:
        if g.get("passed"):
            g.pop("note", None)
            continue
        g["note"] = reported_cause(g.get("stdout") or "") or reported_cause(g.get("stderr") or "")

@dataclass
class GateVerdict:
    """The THREE-state verdict over a gate run (#74 leg 7 / kit #93 PART 2).

    GREEN / DEGRADED / UNKNOWN, and `None` when the suite was SKIPPED -- a skipped suite
    reports no verdict at all, because `all([])` is True and leaving `healthy` to be
    computed over it would print a green over gates that never ran.

    UNKNOWN is a state of its own and NEVER a pass. A gate that exhausted its budget did not
    run to a verdict, so counting it in the pass total would be the vacuous pass in a new
    coat -- and reporting it as a failure would assert a verdict nobody measured. It is
    therefore in NEITHER the pass total nor the failure list, and it is as LOUD as FAIL:
    `status` is UNKNOWN, `healthy` is False, and the caller prints it. The three populations
    are carried apart so a report can state all three counts instead of collapsing them.

    `healthy` keeps the bool/None shape its existing consumers expect (the telemetry outcome
    derives from it): True ONLY for GREEN, False for DEGRADED and for UNKNOWN, None skipped.
    """

    status: str | None
    healthy: bool | None
    all_known_pass: bool | None
    passed: list[dict[str, Any]]
    failed: list[dict[str, Any]]
    unknown: list[dict[str, Any]]

    @property
    def examined(self) -> int:
        """Gates that RAN TO A VERDICT -- the population the pass total is taken over."""
        return len(self.passed) + len(self.failed)

    @property
    def total(self) -> int:
        """Every gate the runner attempted, UNKNOWN included."""
        return self.examined + len(self.unknown)

def judge_gates(
    gate_results: list[dict[str, Any]], cadence_ok: bool = True, gates_ran: bool = True
) -> GateVerdict:
    """Reduce gate results to the three-state verdict, keeping the populations apart.

    PURE over its inputs and free of I/O, so a probe can drive it with synthetic results --
    including a timed-out gate -- without running the suite it judges.

    A FAIL is a MEASURED statement and outranks an UNKNOWN, which is the absence of one:
    when a gate genuinely failed, DEGRADED is the verdict, and the UNKNOWN gates are still
    counted and named on the headline beside it rather than swallowed.
    """
    if not gates_ran:
        return GateVerdict(None, None, None, [], [], [])
    unknown = [g for g in gate_results if g.get("unknown")]
    failed = [g for g in gate_results if not g.get("unknown") and not g["passed"]]
    passed = [g for g in gate_results if not g.get("unknown") and g["passed"]]
    # The pass total is taken over the gates that ran to a verdict. An UNKNOWN is in neither
    # column: not a pass (that is the vacuous green) and not a failure (that is a verdict
    # nobody measured). `all([])` is True, which is right -- no gate ran to a verdict and no
    # gate failed -- and the status is UNKNOWN, so it is not a green.
    all_known_pass = all(g["passed"] for g in passed + failed)
    if failed or not cadence_ok:
        status = "DEGRADED"
    elif unknown:
        status = "UNKNOWN"
    else:
        status = "GREEN"
    return GateVerdict(status, status == "GREEN", all_known_pass, passed, failed, unknown)

def render_status_line(verdict: GateVerdict) -> str:
    """The headline. THREE states, and UNKNOWN is never rendered as a green (#74 leg 7).

    `HEALTHY` stays in the green line because this factory's law, docs and close-row prose
    all name that state by it, while `GREEN` is the canonical three-state term -- so the line
    carries BOTH and no reader has to know which vocabulary the other surface was written in.

    UNKNOWN is printed with the population that produced it (`N of M`), because a third state
    that renders without its count is indistinguishable from one that never fired.
    """
    if verdict.status is None:
        return "Status: GATES SKIPPED (metrics only)"
    if verdict.status == "GREEN":
        return (
            f"Status: HEALTHY (GREEN) — all {verdict.examined} gate(s) that ran to a "
            f"verdict passed"
        )
    if verdict.status == "UNKNOWN":
        return (
            f"Status: UNKNOWN — {len(verdict.unknown)} of {verdict.total} gate(s) "
            f"exhausted their budget and returned NO verdict; they are counted in no "
            f"pass total, and this is NOT a green"
        )
    line = f"Status: DEGRADED (FAIL) — {len(verdict.failed)} gate(s) failed"
    if verdict.unknown:
        line += (
            f", and {len(verdict.unknown)} more exhausted their budget "
            f"(UNKNOWN, no verdict taken)"
        )
    return line

def render_gate_line(g: dict[str, Any]) -> str:
    """One gate's verdict line: the cause and the state co-located (#74 leg 7).

    THREE marks, not two: a gate that exhausted its budget is UNKNOWN -- it neither passed
    nor failed, and printing FAIL would assert a verdict nobody measured. stdout first for
    the cause (a failing pytest gate writes its summary there), then stderr, which is where a
    KILLED or crashed gate states its reason.

    The `({dur}s)` parenthesised token KEEPS its exact position and shape, because
    `tests/test_audit_stamp_duration.py` parses this table with `\\((\\d+(?:\\.\\d+)?)s\\)` to
    sum the durations the run actually printed -- a budget or a cause INSIDE those parens
    would stop the pattern matching and turn that gate's own positive control red. The budget
    and the cause are therefore appended AFTER the token, where they add a fact without
    moving the one a reader (or a gate) already reads.
    """
    mark = "UNKNOWN" if g.get("unknown") else ("PASS" if g["passed"] else "FAIL")
    dur = f">={g['duration_sec']}" if g.get("duration_is_lower_bound") else f"{g['duration_sec']}"
    line = f"  [{mark}] {g['cmd']} ({dur}s)"
    budget = g.get("budget_sec")
    if budget:
        line += f" — budget {budget:.2f}s"
    if mark != "PASS":
        cause = g.get("note") or ""
        if cause:
            line += f" — {cause}"
    if g.get("retried"):
        line += f" [retried once — {g.get('attempts', 2)} attempt(s)]"
    return line

def execute_mechanical_gates(repo_root: Path) -> list[dict[str, Any]]:
    """Execute all discovered mechanical gates."""
    gates_to_run: list[list[str]] = []

    # 1. Ledger verify
    if (repo_root / "tools/ledger.py").is_file():
        gates_to_run.append([sys.executable, "tools/ledger.py", "verify"])

    # 2. Ontology gate
    if (repo_root / "tests/test_ontology.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_ontology.py"])

    # 3. Rework gate
    if (repo_root / "tests/test_rework.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_rework.py"])

    # 4. Single-writer gate
    if (repo_root / "tests/test_single_writer.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_single_writer.py"])

    # 5. Ledger schema & domain invariant gate
    if (repo_root / "tests/test_ledger_schema.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_ledger_schema.py"])

    # 6. Template sync gate
    if (repo_root / "tests/test_template_sync.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_template_sync.py"])

    # 7. Workspace hygiene — reap first, then audit.
    #
    # The order is the whole point. This factory's OWN thin-trigger crons write
    # their redirect logs into /tmp/<namespace>-* on every fire, so an
    # audit-only cadence is guaranteed to go RED within a day of any clean run:
    # the gate would be reporting the very cadence that produces the litter.
    # Reaping first makes the audit a verdict on what SURVIVED the GC rather
    # than on what arrived since the last one, while a genuinely stranded
    # artifact — working-tree litter, or a leaked file the GC cannot remove —
    # still fails the audit that follows.
    #
    # The reap is itself a gate, and that is deliberate: if the GC cannot
    # complete, the run is RED. A reap that fails silently would let a namespace
    # nobody cleaned read exactly like a namespace that is clean.
    if (repo_root / "tools/hygiene.py").is_file():
        gates_to_run.append(
            [sys.executable, "tools/hygiene.py", "--clean",
             "--namespace", hygiene_namespace(repo_root)]
        )
        gates_to_run.append(
            [sys.executable, "tools/hygiene.py", "--audit",
             "--namespace", hygiene_namespace(repo_root)]
        )

    # 8. Visual roadmap and process-to-product matrix audit
    if (repo_root / "tools/roadmap.py").is_file():
        gates_to_run.append([sys.executable, "tools/roadmap.py", "--audit"])

    # 9. Yield-artifact consistency (#38) -- the run row and the scorecard must state
    # ONE yield for one run. It runs as a GATE, not merely as a test: CI is paths-filtered
    # to src/**, so a guard living only under tests/ would never execute on a tools/ change
    # -- which is the same shape as the defect it guards.
    if (repo_root / "tests/test_audit_yield.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_audit_yield.py"])

    # 10. Gate-note population (2026-09-25) -- the scorecard must state the
    # POPULATION a failing gate reported, not its final item. Measured: the
    # schema gate found 3 violations and the scorecard named 1, so a triager
    # repairing that row would believe the gate done. It runs as a GATE for the
    # same reason as #9: nothing under tests/ is reached by CI here.
    if (repo_root / "tests/test_audit_gate_note.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_audit_gate_note.py"])

    # 11. Board-close recorded (2026-09-26, #40) -- a close row must record the board
    # close it observed, not only the ledger transition. Measured: #49's close row was
    # written 3h52m54s before the board issue was actually closed, so the board read as
    # free-and-actionable and a gated trigger fired on it. Offline by necessity -- a gate
    # cannot call gh, so the settling lane records the state it saw and the gate asserts
    # it was recorded. Runs as a GATE for the same reason as #9 and #10: nothing under
    # tests/ is reached by CI here.
    if (repo_root / "tests/test_close_board_recorded.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_close_board_recorded.py"])

    # 12. Kit pin (2026-09-27) -- the tree judged against the kit state it VENDORED, not
    # against the meta-factory's live manifest. The direction of the verdict is the whole
    # point: a gate against someone else's manifest reds our audit when THEY move, for a
    # change we never took; a gate against our own pin reds only when our tree diverges
    # from our own declaration, which is the only thing we can act on. ABSENT is not a
    # verdict -- we port a subset -- so the gate judges only carried paths, and a
    # deliberate fork is a declaration in registry/kit-exemptions.json, never a silent
    # divergence. Measured on landing: 14 carried paths judged, 6 declared exempt.
    if (repo_root / "tests/test_kit_pin.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_kit_pin.py"])

    # 13. Cron thinness (2026-09-27, pacemaker instrument) -- every enabled cron row
    # must either wake a lane (deliver_to session:*, or a notify in the prompt) or
    # carry the wake-only marker; a row that does neither runs its work inside its own
    # session and cannot be seen by any lane. Measured on adoption: 10 problems over
    # 12 enabled ai-antispam rows. Pure predicate -- no sqlite3/subprocess/socket import
    # is permitted in it, so it reads rows handed to it and never the live table.
    # Runs as a GATE for the same reason as #9-#12: nothing under tests/ is reached by CI.
    if (repo_root / "tests/test_cron_thinness.py").is_file():
        # INVOKED THROUGH PYTEST, NOT AS A BARE SCRIPT, and the reason is measured:
        # the file defines 16 pytest test functions and carries NO `__main__`, so the
        # script form executes zero checks and exits 0. Registered as a script it read
        # 0.16s / rc=0 -- a green over a population it never examined, while the pytest
        # form runs 16 checks in ~16s. A bare-script registration here is a vacuous
        # pass, not a fast one. (Same class as the pytest-mode close-preflight gate;
        # #64's per-gate wall makes the honest invocation affordable.)
        gates_to_run.append(
            [sys.executable, "-m", "pytest", "tests/test_cron_thinness.py", "-q"]
        )

    # 14. The patrol RUNNER's own wiring, as its own row (#163, adoption round).
    # Registered separately from gate 13 because tests/gate_registry.py couples per FILE:
    # the cron predicate and the patrol runner are two rows, and one row covering the pair
    # would let a lane unregister the runner while the predicate still reads as covered.
    # The runner is the live surface (it reads cron rows, the ledger, the log store and the
    # remote), so a runner whose leg is silently NOT RUN reports the same shape as a runner
    # whose legs all ran and found nothing -- which is the failure class this gate's own
    # probes exist to prevent. Measured on adoption: rc=0, 102 checks, and CI's own
    # invocation 912 passed / 0 failed, so it is safe to hold in the audit AND in pytest.
    if (repo_root / "tests/test_patrol_host_state.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_patrol_host_state.py"])

    # 15. The ledger instrument's own gate, registered because IT asserts its registration:
    # an unregistered gate never runs (P29), and the adopted copy fails on exactly that check.
    # Wall: 4x its measured 24.99s. The default 30.0 is a 1.20x factor, not a margin, which is
    # the flake ai-antispam#64 exists to remove -- so this row carries its own budget
    # (GATE_WALLS_SEC above) rather than inheriting a wall it does not fit.
    if (repo_root / "tests/test_ledger.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_ledger.py"])

    # 16. The stamp's own duration (#71) -- the run row's `duration=` must be MEASURED, not
    # the literal `4s` it carried for 15 consecutive rows, and it must track the gate work
    # the same run printed. Runs as a GATE for the same reason as #9-#15: nothing under
    # tests/ is reached by CI here, so a guard living only in the suite would never execute
    # on a tools/audit.py change -- the exact shape of the defect it guards.
    #
    # SCRIPT-form, and the file is script-form BY CONSTRUCTION: it carries `main()` under a
    # `__main__` guard and NO module-level `def test_*`. Direction 5 of
    # `tests/test_gate_registration.py` reads the form from the TARGET's shape (#124, probed
    # at `:754`), so a test-carrying file would have to be registered under a pytest runner
    # -- and pytest is the expensive path here, dragging in `tests/conftest.py` and
    # `pythonpath = src` for ~46s against this file's ~10s script run.
    if (repo_root / "tests/test_audit_stamp_duration.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_audit_stamp_duration.py"])

    # The commit-citation clause. SCRIPT-MODE (0 test functions collected, `__main__` present),
    # so a script registration is the honest form -- and unlike the cron gate this one is NOT
    # vacuous: it prints "examined N commit(s)" and exits 1 on an unexcused violation.
    #
    # It read RED here until 2026-09-28, and the fix was two-part: the gate now takes its MARKER
    # from factory data (the kit's own sha does not resolve in this history), and the three
    # pushed subjects in cd2a3f87..HEAD are admitted as exemptions with their empty-repair-space
    # proofs. Registering it is what makes those exemptions PRINTED on every audit -- visible
    # debt rather than a silent pass.
    if (repo_root / "tests/test_ledger_commit_cites_no_rows.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_ledger_commit_cites_no_rows.py"])

    # tests/test_ledger_close_preflight.py is DELIBERATELY NOT registered here, and the reason is
    # measured rather than a preference -- it is registered where it already runs.
    #
    # (1) CI RUNS IT. deploy.yml:69 is `uv run pytest --ignore=tests/integration -x`, which collects
    #     its 7 test functions. Registering it here would run the same gate twice per audit.
    # (2) THE WALL DOES NOT FIT IT. run_gate hardcodes timeout=30; this gate measured 24.35s wall
    #     on an unloaded box (its own pytest time is 4.66s -- the rest is interpreter+plugin
    #     startup under a cold cache). That is a 1.23x factor, not a margin, so it flakes under
    #     load -- and flakes into the WORST shape: the except branch reports duration_sec 0.0 with
    #     the exception in stderr, so a timeout is indistinguishable from an instant logic error.
    # (3) It is pytest-MODE only (no __main__), so a bare-script registration would exit 0 having
    #     executed ZERO checks -- a decorative green, the class this file's own gate 10 exists for.
    #
    # The fork's real gap is that this audit hardcodes its wall where the kit's reads
    # tools/gate_budget.py + registry/gates.json (9 refs there, 0 here). Tracked as ai-antispam#64;
    # porting the budget reader is what would let this gate be registered honestly.

    results = []
    for cmd in gates_to_run:
        wall = _gate_wall(cmd)
        if wall == DEFAULT_GATE_WALL_SEC:
            print(f"gate budget: {_gate_wall_key(cmd)} uses the declared default {DEFAULT_GATE_WALL_SEC}s")
        results.append(run_gate(cmd, repo_root, budget_sec=wall))
    return results


def check_cadence_integrity(ledger_path: Path) -> dict[str, Any]:
    """Check whether recurring processes ran according to declared schedule."""
    if not ledger_path.is_file():
        return {"cadence_held": False, "hours_since_last_run": None}

    now = datetime.datetime.now(datetime.timezone.utc)
    last_ts = None
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    data = json.loads(line)
                    if data.get("event") in ("run", "score"):
                        last_ts = data.get("ts")
                except Exception:
                    pass

    if not last_ts:
        return {"cadence_held": True, "hours_since_last_run": None}

    try:
        t_last = datetime.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        hours_diff = (now - t_last).total_seconds() / 3600.0
        # Expected daily cadence: interval <= 30 hours
        cadence_held = hours_diff <= 30.0
        return {
            "cadence_held": cadence_held,
            "hours_since_last_run": round(hours_diff, 1),
            "last_run_ts": last_ts,
        }
    except Exception:
        return {"cadence_held": False, "hours_since_last_run": None}


def gate_note(g: dict[str, Any], max_chars: int = GATE_CAUSE_LIMIT) -> str:
    """One-line summary of a gate's outcome, for the scorecard's gate table.

    A PASSing gate prints a single summary line, so its LAST line is the summary.
    A FAILing gate prints a header carrying its POPULATION -- "N problem(s)" --
    and then one line per problem, so its last line is only the FINAL item.
    Reporting that line states 1 problem where the gate found N, and a triager
    who repairs the named row believes the gate done. Measured 2026-09-25:
    tests/test_ledger_schema.py found 3 unauthorized-actor violations and the
    scorecard named one of them (line 30). A FAIL therefore carries its count.

    The FAIL branch DELEGATES to `reported_cause`, which is the ONE cause predicate
    (a second implementation of the same job is a second thing to keep in step, and
    they drift silently -- AGENTS.md rule 14). What stays here is only what is
    specific to THIS surface: the PASS short-circuit (a passing gate's output must
    never be scanned for a count) and the `|` -> `/` escape the markdown cell needs,
    since a raw pipe in a table cell splits the row.
    """
    if not g["passed"]:
        stream = g["stdout"] or g["stderr"]
        return reported_cause(stream, limit=max_chars).replace("|", "/")
    rows = [" ".join(ln.split()) for ln in (g["stdout"] or g["stderr"]).splitlines() if ln.strip()]
    if not rows:
        return ""
    return rows[-1].replace("|", "/")[:max_chars]

def format_report_markdown(
    date_str: str,
    ledger_stats: dict[str, Any],
    rework_stats: dict[str, Any],
    cadence_stats: dict[str, Any],
    gate_results: list[dict[str, Any]],
) -> str:
    """Format the full audit into markdown document."""
    # The report's headline is the SAME three-state verdict the stdout line and the stamp row
    # carry (#72), taken from the ONE reduction rather than re-derived here: a second
    # `all(g["passed"])` would count a timed-out gate as a FAILURE, which is the defect this
    # fix removes, and it would disagree with the JSON payload printed by the same run.
    report_verdict = judge_gates(gate_results, cadence_ok=cadence_stats.get("cadence_held", True))
    if report_verdict.status is None:
        verdict = "SKIPPED"
    elif report_verdict.status == "GREEN":
        verdict = "PASSED"
    else:
        verdict = report_verdict.status

    lines = [
        f"# Operational Process Self-Audit — {date_str}",
        "",
        f"> **Verdict:** `{verdict}` · Process 3 (Internal Self-Audit)",
        "",
        "---",
        "",
        "## 1. Process Health & Delivery Telemetry",
        "",
        "| Metric | Value | Reference / Derivation |",
        "|---|---|---|",
        f"| **Total Ledger Events** | `{ledger_stats.get('total_events', 0)}` | Continuous ledger sequence |",
        f"| **Closed Tasks** | `{ledger_stats.get('closed_tasks', 0)}` | Tasks reaching verified close |",
        f"| **First-Pass Yield** | `{first_pass_yield_pct(ledger_stats.get('runs_by_outcome', {}).get('accepted', 0), ledger_stats.get('run_events', 0))}%` | Accepted runs ÷ total runs |",
        f"| **Rework Entries** | `{rework_stats.get('total_entries', 0)}` | Defect count recorded in rework.md |",
        f"| **Rework Rate** | `{round(rework_stats.get('rework_rate', 0.0) * 100, 1)}%` | Rework entries ÷ closed tasks |",
        f"| **Avg Task Lead Time** | `{ledger_stats.get('avg_lead_time_sec', 0.0)}s` | Average duration from intake to close |",
        f"| **Total Inference Cost** | `${ledger_stats.get('total_cost_usd', 0.0):.4f}` | Tracked cost across ledger task telemetry |",
        f"| **Avg Cost / Closed Task** | `${ledger_stats.get('avg_cost_per_closed_task_usd', 0.0):.4f}` | Total cost ÷ closed tasks |",
        f"| **Total Tokens (In/Out)** | `{ledger_stats.get('total_tokens_in', 0)} / {ledger_stats.get('total_tokens_out', 0)}` | Cumulative prompt and completion tokens |",
        f"| **Cadence Status** | `{'HELD' if cadence_stats.get('cadence_held') else 'MISSED'}` | Last run: {cadence_stats.get('hours_since_last_run')}h ago |",
        "",
        "---",
        "",
        "## 2. Mechanical Gate Verification",
        "",
        "| Gate / Command | Outcome | Duration | Notes |",
        "|---|---|---|---|",
    ]

    for g in gate_results:
        # Three marks, matching the stdout table and the JSON payload (#72). `UNKNOWN` is
        # neither pass nor fail: the gate exhausted its budget and returned no verdict.
        status = "UNKNOWN" if g.get("unknown") else ("PASS" if g["passed"] else "FAIL")
        dur = f">={g['duration_sec']}s" if g.get("duration_is_lower_bound") else f"{g['duration_sec']}s"
        lines.append(f"| `{g['cmd']}` | `{status}` | `{dur}` | {gate_note(g)} |")

    # A non-PASS row above is truncated for the table, so the population it reports
    # is repeated in full here. Repair from this section, never from the table.
    # UNKNOWN rows are listed beside the FAILs rather than dropped: a gate that took no
    # verdict is exactly the one a reader must look at, and a section that only carried
    # FAILs would make a timeout invisible in the artifact the day's record keeps. The
    # HEADER keeps its pre-#72 wording because `tests/test_audit_gate_note.py` pins the
    # literal `### 2.1 Failing gate output (full)`; the per-row label carries the
    # three-state distinction instead, which is where a reader looks anyway.
    non_passing = [g for g in gate_results if not g["passed"]]
    if non_passing:
        lines.extend([
            "",
            "### 2.1 Failing gate output (full)",
            "",
        ])
        for g in non_passing:
            body = (g["stdout"] or g["stderr"] or "(no output)").strip()
            fence = "````" if "```" in body else "```"
            label = "UNKNOWN (no verdict)" if g.get("unknown") else "FAIL"
            lines.extend([
                f"**`{g['cmd']}`** -- {label}, rc={g['exit_code']}, {g['duration_sec']}s",
                "",
                fence,
                body,
                fence,
                "",
            ])

    lines.extend([
        "",
        "---",
        "",
        "## 3. Calibration Spot-Check (Sampled Task)",
        "",
    ])

    sc = ledger_stats.get("spot_check")
    if sc:
        seq_ok = sc.get("sequence_intact")
        lines.extend([
            f"- **Sampled Subject:** `{sc.get('subject')}`",
            f"- **Intake Recorded:** `{'YES' if sc.get('intake_verified') else 'NO'}`",
            f"- **Claim Lock Recorded:** `{'YES' if sc.get('claim_verified') else 'NO'}`",
            f"- **Close Verified:** `{'YES' if sc.get('close_verified') else 'NO'}`",
            f"- **Sequence Integrity:** `{'VERIFIED' if seq_ok else 'BROKEN'}`",
        ])
    else:
        lines.append("*No closed tasks available to spot-check.*")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Operational process audit runner.")
    parser.add_argument("--json", action="store_true", help="Output JSON results to stdout")
    parser.add_argument("--report", action="store_true", help="Generate dated markdown report in evidence/scores/")
    parser.add_argument("--output", type=str, default="", help="Custom report output file path")
    parser.add_argument("--stamp", action="store_true", help="Record run row into evidence/ledger.jsonl")
    parser.add_argument("--actor", type=str, default="hq", help="Actor role for ledger stamp (default: hq)")
    args = parser.parse_args()

    ledger_file = REPO_ROOT / "evidence/ledger.jsonl"
    rework_file = REPO_ROOT / "evidence/rework.md"

    ledger_stats = parse_ledger(ledger_file)
    rework_stats = parse_rework(rework_file, ledger_stats.get("closed_tasks", 0))
    cadence_stats = check_cadence_integrity(ledger_file)

    # WALL-CLOCK AROUND THE GATE LOOP (#71). The stamp row's `duration=` was the string
    # literal `4s`, so every daily self-audit row carried a duration the audit never
    # measured -- plausible, constant and wrong, which a reader comparing runs reads as a
    # stable cost while the run itself takes minutes. The population is the SAME one the
    # gate table below reports (the loop that runs the gates), measured the same way
    # `run_gate` measures each gate, so the row and the table describe one run.
    t_gates0 = datetime.datetime.now()
    gate_results = execute_mechanical_gates(REPO_ROOT)
    gate_loop_sec = (datetime.datetime.now() - t_gates0).total_seconds()

    # THREE states, not two (#72 / #74 leg 7). A gate that exhausted its budget did not run
    # to a verdict, so `all(g["passed"])` would have counted it as a FAILURE -- a verdict
    # nobody measured -- which is how a healthy gate under co-tenant load turned the daily
    # audit DEGRADED. `judge_gates` keeps UNKNOWN in its own population and the status line
    # says so. `attach_gate_causes` fills each non-passing gate's `note` ONCE here, so the
    # JSON payload, the markdown report and the stdout table cannot disagree about what a
    # gate said.
    attach_gate_causes(gate_results)
    cadence_ok = cadence_stats.get("cadence_held", True)
    verdict = judge_gates(gate_results, cadence_ok=cadence_ok)
    healthy = bool(verdict.healthy)

    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    if args.json:
        payload = {
            "date": today,
            "healthy": healthy,
            "status": verdict.status,
            "all_gates_pass": verdict.all_known_pass,
            "unknown_gates": len(verdict.unknown),
            "cadence": cadence_stats,
            "delivery": ledger_stats,
            "rework": rework_stats,
            "gates": gate_results,
        }
        print(json.dumps(payload, indent=2))
        return 0 if healthy else 1

    # Stamp BEFORE reporting (#38) — a scorecard is a statement about the ledger it is
    # written into, so the run row must exist before the figures are taken. Reporting
    # first made every scorecard one row stale BY CONSTRUCTION: its "Total Ledger
    # Events" and its First-Pass Yield described the ledger as it stood BEFORE the run
    # being reported, and the error ran in the favourable direction — the run that just
    # failed was not yet in its own denominator. Order is stamp -> re-parse -> report.
    if args.stamp:
        outcome = "accepted" if healthy else "failed"
        # The gate summary is derived from the GATES, never from `healthy`: a cadence-only
        # failure leaves every gate passing, and calling that `gate-failure` would name the
        # wrong leg. `gate-unknown` is a THIRD token because a gate that exhausted its budget
        # neither passed nor failed, and folding it into `gate-failure` would put a verdict
        # nobody measured into the row (#72).
        if verdict.failed:
            gate_summary = "gate-failure"
        elif verdict.unknown:
            gate_summary = "gate-unknown"
        else:
            gate_summary = "all-pass"
        # The yield this row carries is the POST-run figure — the same population the
        # scorecard below reports — so the two artifacts of one run state one number.
        # It is derived rather than read back because the row must be written before the
        # ledger can be re-read; `healthy` is this run's own outcome, so its contribution
        # to accepted/total is already known at this point.
        pre_runs = ledger_stats.get("run_events", 0)
        pre_accepted = ledger_stats.get("runs_by_outcome", {}).get("accepted", 0)
        post_runs = pre_runs + 1
        post_accepted = pre_accepted + (1 if healthy else 0)
        yield_pct = first_pass_yield_pct(post_accepted, post_runs)
        # MEASURED, never a literal (#71): `gate_loop_sec` is the wall-clock elapsed around
        # the gate loop above, so this field tracks the work the row describes instead of
        # standing at a constant. `turns=0` stays a legitimate declaration -- the runner
        # makes no agent turns -- and is unaffected.
        # `verdict=` carries the THREE-state outcome explicitly (#72): `gate-unknown` alone
        # would be the only place a reader learned a timeout had happened, and a row whose
        # only marker is a compound token is one careless regex away from reading as a
        # failure. The state is named, so the row is self-describing.
        detail = f"duration={round(gate_loop_sec, 2)}s turns=0 outcome={outcome} gate={gate_summary} verdict={verdict.status} yield={yield_pct}%"
        stamp_cmd = [
            sys.executable,
            "tools/ledger.py",
            "append",
            "--event",
            "run",
            "--actor",
            args.actor,
            "--subject",
            "internal-self-audit",
            "--detail",
            detail,
        ]
        res = subprocess.run(stamp_cmd, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if res.returncode == 0:
            print(f"Ledger telemetry stamped: {detail}")
        else:
            print(f"Failed to stamp ledger: {res.stderr.strip()}", file=sys.stderr)

        # Re-read the ledger so the report below describes the state INCLUDING this run.
        # Everything downstream of this line (text summary, markdown report) therefore
        # carries post-run figures; the gate results are deliberately NOT recomputed,
        # since a run row cannot change a gate and re-running them would double the cost.
        # CADENCE IS DELIBERATELY LEFT PRE-STAMP TOO, and that leg is not an optimisation:
        # `cadence_held` is a VERDICT leg, and it asks whether the PREVIOUS run was on time.
        # Recomputing it after this row lands would read ~0h since the last run on EVERY
        # run, so the leg would report HELD by construction and a genuinely missed cadence
        # would become unobservable — a silent green in the one place built to go red.
        ledger_stats = parse_ledger(ledger_file)
    # Text summary output
    print(f"=== Factory Operational Self-Audit ({today}) ===")
    # The THREE-state headline (#72). `render_status_line` is the one place the state is
    # spelled, so the stdout line, the JSON `status` and the stamp row's `verdict=` token
    # cannot describe different runs. A timeout reads UNKNOWN here, never DEGRADED: the box
    # was loaded, the gate was healthy, and the audit took no verdict on it.
    print(render_status_line(verdict))
    print(f"  - First-Pass Yield: {first_pass_yield_pct(ledger_stats.get('runs_by_outcome', {}).get('accepted', 0), ledger_stats.get('run_events', 0))}%")
    print(f"  - Closed Tasks: {ledger_stats.get('closed_tasks', 0)} | Intake Tasks: {ledger_stats.get('intake_tasks', 0)}")
    print(f"  - Rework Entries: {rework_stats.get('total_entries', 0)} (Rate: {round(rework_stats.get('rework_rate', 0.0) * 100, 1)}%)")
    print(f"  - Cadence: {'HELD' if cadence_ok else 'MISSED'} (last run: {cadence_stats.get('hours_since_last_run')}h ago)")
    unknown_note = f", {len(verdict.unknown)} UNKNOWN" if verdict.unknown else ""
    print(f"\nMechanical Gates ({len(gate_results)}{unknown_note}):")
    for g in gate_results:
        # `render_gate_line` carries the mark, the elapsed time, the declared budget and the
        # gate's own cause on ONE line, so a reader triaging the table does not have to open
        # the report to learn WHY a gate did not pass (#74 leg 7).
        print(render_gate_line(g))

    if args.report or args.output:
        report_md = format_report_markdown(today, ledger_stats, rework_stats, cadence_stats, gate_results)
        out_path = Path(args.output) if args.output else (REPO_ROOT / f"evidence/scores/{today}-self-audit.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_md, encoding="utf-8")
        print(f"\nAudit report written to: {out_path}")

    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
