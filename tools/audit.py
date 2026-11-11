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
import math
import os
import re
import subprocess
import sys
import time
from collections import Counter
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
from field_predicate import (  # noqa: E402
    declared_cause_count,
    declared_outcome_values,
    declared_rework,
    declared_telemetry,
    mentioned_telemetry,
)
from gate_budget import TIMEOUT_EXIT_CODE  # noqa: E402


def format_yield_percent(value: float | None) -> str:
    """The yield as a percentage — THE rounding site, so every surface states one number.

    Origin (#143): one ratio was rendered two ways. The run row's `yield=` TRUNCATED
    (`int(ratio * 100)`) while the report ROUNDED to 1 dp, so a single run published two
    yields — and the divergence is not a large-denominator curiosity: modelled over all
    `a/d` pairs for d in 1..399, **74,451 of 80,199** diverge, the first at `1/3` (row
    `33%` against report `33.3%`). A fix scoped by denominator threshold would therefore
    be scoped wrong.

    The cure is to remove the SECOND rounding rather than trade truncation for a smaller
    error: this function is the only place a ratio becomes a percentage, and every
    renderer calls it. `None` means no run row stated an outcome, which is `n/a` and
    never `0%` — an unstated yield is UNKNOWN, not a failure.
    """
    return "n/a" if value is None else f"{round(value * 100, 1)}%"


# The `Subject` column's vocabulary, named once. `tests/test_rework.py` enforces the
# same three values on the document; they live here as well because the audit *reads*
# the column to derive a numerator, and a second hand-written pattern would let the
# gate and the reader disagree about what a determinate link looks like.
SUBJECT_NONE = "none"
SUBJECT_PRE_COLUMN = "not recorded (pre-column)"
SUBJECT_WORK_UNIT_RE = re.compile(r"#\d+")


# The `outcome` field's domain, named once (`docs/processes.md`: accepted · reworked ·
# abandoned · failed). Anything outside it is not an outcome, it is a typo.
OUTCOME_DOMAIN = ("accepted", "reworked", "abandoned", "failed")

def declared_outcome(detail: str) -> tuple[str, str | None]:
    """What the row DECLARES about its outcome: (bucket, problem).

    The bucket is one of the four domain values, or `"unstated"` when the row
    declares no outcome at all, or `"invalid:<value>"` when it declares one outside
    the domain. `problem` is a readable report for the two non-domain cases.

    Three rules. The first is #53 (ruling n=333 clause 4); the other two are its
    consequences, and the third was not delivered until #498 conformed this reader.

    * **Read the CANONICAL TRAILER, not `outcome=` in free prose.** The read is
      `field_predicate.declared_outcome_values` — the same positional read
      (`trailer_tokens` -> `keyed_value`) that `declared_rework`, `declared_duty` and
      `declared_telemetry_provenance` compose. It was NOT always so: until #498 this
      function hand-rolled a scan of the WHOLE detail and took the LAST `outcome=`
      token, which equals the trailer read only while the trailer is intact AND
      terminal. The DOMAIN stays here, beside `OUTCOME_DOMAIN`; only the READ moved,
      because a bucket is a classification and not a read.
    * **An earlier mention is prose, not a declaration.** `#53`'s live instance was a
      close row whose prose read `outcome=accepted.`, and the sentence's full stop
      travelled inside the value — which dropped a genuinely accepted work unit out of
      the success set.
    * **A token OUTSIDE the run is never a value at all.** The old reader cured the
      `#53` direction and left its mirror: measured at 3435 rows (#498), 61 rows
      diverged from the positional read, every one of them on a token outside its run,
      and the fabrications run BOTH ways — `n=2028` declares `telemetry=unavailable`
      and read as `failed` off prose quoting another row's trailer, `n=3275` declares
      `duty=completed` and read as `accepted` off a quotation, `n=1062` states it
      deliberately carries no `outcome=` and read as declaring `,`.

    An EMPTY value is a MENTION, not a declaration: `keyed_value` refuses an empty value
    by design, so `outcome=` states nothing even inside the run and the row reads
    UNKNOWN. A malformed value must be IN the run to be reported at all.

    An unstated outcome is UNKNOWN. It is never `accepted`: that default is the
    defect this clause removes, and it is why the two headline rates could only
    report success.
    """
    values = declared_outcome_values(detail)
    if not values:
        return "unstated", None
    value = values[-1]
    if value in OUTCOME_DOMAIN:
        return value, None
    return (
        f"invalid:{value}",
        f"outcome value {value!r} is outside the domain {OUTCOME_DOMAIN} — "
        f"reported, never bucketed as a verdict",
    )

def rework_bucket(value: str) -> str:
    """The FORM of a declared `rework` value — one bucket, never a verdict (#106).

    `work_unit` — a reference to the entry this close produced (`#N`), tested with the
    same `SUBJECT_WORK_UNIT_RE` the rework log's Subject column uses, so the count here
    and the gate that RESOLVES those references agree by construction. `none` — the close
    states it produced no entry. `unstated` — the row carries the absence token itself
    (#53 clause 2: an unrecorded disposition is UNKNOWN, never a value). `invalid:<value>`
    — anything else, reported by name rather than bucketed as a disposition.

    Classification lives here and not in `field_predicate.py` because it needs this
    module's vocabulary (`SUBJECT_WORK_UNIT_RE`, `SUBJECT_NONE`) and importing upward
    would invert the dependency; the POSITIONAL read stays there, where it is shared.
    `tests/test_rework_declared_landed.py` imports THIS function, so a value is never
    classified two ways.
    """
    if SUBJECT_WORK_UNIT_RE.fullmatch(value):
        return "work_unit"
    if value == SUBJECT_NONE:
        return "none"
    if value == "unstated":
        return "unstated"
    return f"invalid:{value}"

def format_yield_percent(value: float | None) -> str:
    """The yield as a percentage — THE rounding site, so every surface states one number.

    Origin (#143): one ratio was rendered two ways. The run row's `yield=` TRUNCATED
    (`int(ratio * 100)`) while the report ROUNDED to 1 dp, so a single run published two
    yields — and the divergence is not a large-denominator curiosity: modelled over all
    `a/d` pairs for d in 1..399, **74,451 of 80,199** diverge, the first at `1/3` (row
    `33%` against report `33.3%`). A fix scoped by denominator threshold would therefore
    be scoped wrong.

    The cure is to remove the SECOND rounding rather than trade truncation for a smaller
    error: this function is the only place a ratio becomes a percentage, and every
    renderer calls it. `None` means no run row stated an outcome, which is `n/a` and
    never `0%` — an unstated yield is UNKNOWN, not a failure.
    """
    return "n/a" if value is None else f"{round(value * 100, 1)}%"

def format_elapsed_seconds(elapsed_secs: float) -> str:
    """The run row's `duration=` value, rendered from the run's OWN measured wall time.

    `duration=` is a TELEMETRY key — `docs/instruments/ledger.md` names it among the
    measurements the tool supplies — so a value in it must be one the emitting path
    measured. This function is the ONE site that renders it, and it takes the number as
    an argument precisely so no literal can live in the key: a constant here would be a
    fabricated measurement, which is not repaired by being small (#276, the #130 class).

    Integer seconds, matching every sibling run row's form (`duration=3s`, `duration=0s`).
    A sub-second run renders `0s`, which is a MEASUREMENT and not a floor: the audit
    stamps its row after the whole gate suite, so `0s` is a fact about a run that took
    less than a second, never a default standing in for an unmeasured one.
    """
    return f"{int(elapsed_secs)}s"

def format_run_row_detail(
    stats: dict[str, Any], outcome: str, gate_summary: str, elapsed_secs: float
) -> str:
    """The self-audit run row's canonical detail — the ONE site that renders its `yield=`.

    Factored out so the row is PROBEABLE (#143): the row and the report disagreed because
    each rendered the ratio itself, and a probe that can only reach the report cannot catch
    the row's arithmetic. This function and the report's renderers all call
    `format_yield_percent`, so their agreement is an identity rather than a coincidence.

    `duration=` is rendered from `elapsed_secs` — the caller's own measurement of the run
    — and never from a literal (#276). `turns=0` is honest: a mechanical run takes no agent
    turns, so it stays.
    """
    return (
        f"duration={format_elapsed_seconds(elapsed_secs)} turns=0 outcome={outcome} "
        f"gate={gate_summary} "
        f"yield={format_yield_percent(stats.get('first_pass_yield'))}"
    )

def format_yield_rate(stats: dict[str, Any]) -> str:
    """The yield as a percentage, or `n/a` when no run row states an outcome."""
    return format_yield_percent(stats.get("first_pass_yield"))

def format_yield_text(stats: dict[str, Any]) -> str:
    """First-Pass Yield with its population and its coverage (clause 5).

    A rate is never printed alone. The undeclared rows are excluded from the ratio
    and COUNTED BESIDE IT, because counting them as accepted is the defect and
    counting them as failures would be a second fabrication in the other direction.
    The coverage is a printed disclosure, never a gate (clause 6): against
    append-only historical rows a threshold would be permanently RED with no lawful
    repair, which is the empty-repair-space defect ruled at n=328.
    """
    value = stats.get("first_pass_yield")
    population = stats.get("first_pass_yield_population", 0)
    total = stats.get("run_events", 0)
    accepted = stats.get("runs_by_outcome", {}).get("accepted", 0)
    rate = ("n/a — no run row states an outcome" if value is None
            else format_yield_percent(value))
    return (
        f"{rate} ({accepted} accepted runs ÷ {population} runs stating an outcome "
        f"— coverage: {population} of {total} run rows)"
    )

def format_rework_declaration_note(stats: dict[str, Any]) -> str:
    """The declaration count's buckets, so the share is never read alone (#106).

    Every bucket is printed. A row carrying `rework=unstated` is UNDECLARED, not a
    declaration — and a value outside the vocabulary is reported by name, because a
    malformed declaration silently counted as one is the defect the marker exists to
    make visible. The note states the PREDICATE with the number: the canonical trailer,
    read by position.
    """
    buckets = stats.get("rework_declarations_by_bucket", {}) or {}
    undeclared = stats.get("close_events", 0) - stats.get("close_rows_declaring_rework", 0)
    parts = [
        f"rework=#N {buckets.get('work_unit', 0)}",
        f"rework=none {buckets.get('none', 0)}",
        f"undeclared {undeclared}",
    ]
    if buckets.get("unstated", 0):
        parts.append(f"of which state rework=unstated {buckets.get('unstated', 0)}")
    invalid = stats.get("invalid_rework_reports", []) or []
    if invalid:
        parts.append(f"invalid {len(invalid)}")
    return (
        "canonical trailer read by position: " + "; ".join(parts)
        + " (printed, never gated — n=386 clause 5)"
    )

def format_yield_note(stats: dict[str, Any]) -> str:
    """The yield's population and coverage, as the note column states it (clause 5)."""
    accepted = stats.get("runs_by_outcome", {}).get("accepted", 0)
    population = stats.get("first_pass_yield_population", 0)
    total = stats.get("run_events", 0)
    return (
        f"{accepted} accepted runs ÷ {population} runs stating an outcome "
        f"(coverage: {population} of {total} run rows)"
    )

def format_cost_usd(value: float | None) -> str:
    """A per-task cost figure — `n/a` when the ratio is UNDEFINED, never `$0.0000`.

    Origin (#620): a zero denominator published `0.0`, which renders as `$0.0000` — "a
    successful task costs nothing", the most favourable possible value for the least
    measurable state. The yield already carries the three-state contract (`None` -> `n/a`);
    a cost ratio whose denominator is zero is undefined in exactly the same way, so it
    travels the same shape rather than defaulting to a number nobody measured.
    """
    return "n/a" if value is None else f"${value:.4f}"

def format_cost_per_success_text(stats: dict[str, Any]) -> str:
    """Cost / Successful Task with its population and its coverage (clause 5)."""
    return (
        f"{format_cost_usd(stats.get('cost_per_successful_task_usd'))} "
        f"(${stats.get('total_cost_usd', 0.0):.4f} total cost ÷ "
        f"{stats.get('successful_closed_tasks', 0)} accepted closed subjects "
        f"— coverage: {stats.get('close_rows_stating_outcome', 0)} of "
        f"{stats.get('close_events', 0)} close rows state an outcome)"
    )

def format_cost_note(stats: dict[str, Any]) -> str:
    """Cost / Successful Task's population and coverage, for the note column."""
    return (
        f"${stats.get('total_cost_usd', 0.0):.4f} total cost ÷ "
        f"{stats.get('successful_closed_tasks', 0)} accepted closed subjects "
        f"(coverage: {stats.get('close_rows_stating_outcome', 0)} of "
        f"{stats.get('close_events', 0)} close rows state an outcome)"
    )

def format_out_of_trailer_note(stats: dict[str, Any]) -> str:
    """Which rows carry telemetry-shaped tokens the trailer scope EXCLUDED (issue #90).

    The count alone is a number without a referent: a reader cannot tell a census from
    a measurement without seeing the rows. So the note names them, and says why they
    are out — an exclusion that cannot be audited is indistinguishable from a loss.
    """
    rows = stats.get("out_of_trailer_rows", []) or []
    if not rows:
        return "none — every telemetry-shaped token in the ledger sits inside a canonical trailer"
    named = ", ".join(f"n={row.get('n')} ({row.get('event')})" for row in rows)
    return (
        "excluded from the totals above BY DESIGN: these rows carry a telemetry-shaped "
        f"token outside the canonical trailer — {named}. Read each as prose, not as spend"
    )

def format_undeclared_text(stats: dict[str, Any]) -> str:
    """What the ledger does NOT say, reported rather than absorbed (clause 4)."""
    reports = stats.get("invalid_outcome_reports", []) or []
    invalid = (
        f"; {len(reports)} invalid value(s) reported"
        if reports
        else "; no invalid value reported"
    )
    return (
        f"{stats.get('unstated_run_rows', 0)} run rows and "
        f"{stats.get('unstated_close_rows', 0)} close rows state none — UNKNOWN, "
        f"never accepted{invalid}"
    )

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


def tree_condition(repo_root: Path) -> dict[str, Any]:
    """The tree condition this run READ, so a verdict carries its own provenance (#150).

    The audit reads the WORKING TREE -- it must, since that is the only tree a run has --
    so a peer lane's half-written file produces a verdict about the repository that the
    repository does not have. Measured twice, on the 2026-09-23 and 09-24 runs: a DEGRADED
    verdict naming one failure whose file was VALID at HEAD and invalid only in an
    uncommitted working-tree edit, so a reader took a lane's mid-turn state for a defect
    of the repository.

    The value is computed ONCE and both surfaces print FROM it, never re-deriving it: a
    reader that recomputes the condition can disagree with the run that experienced it
    (HQ ruling n=1167).

    A tree that is not a checkout, or a git that cannot answer, yields `None` for the sha
    and an EMPTY list. An unreadable tree is not a clean one, and `head_sha: None` says so
    rather than rendering as zero modifications.
    """
    head_sha: str | None = None
    modified: list[str] = []
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            head_sha = proc.stdout.strip()
        proc = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if len(line) > 3:
                    modified.append(line[3:].strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {"head_sha": head_sha, "modified_count": len(modified), "modified_paths": modified}

def parse_ledger(ledger_path: Path) -> tuple[dict[str, Any], set[str]]:
    """Parse ledger.jsonl and calculate operational delivery metrics, including token/cost economics.

    Returns the metrics payload *and* the set of distinct closed work units. The set
    travels beside the numbers rather than being re-derived per consumer, because it
    IS the denominator every per-task rate names: handing each rate its own copy of
    the count is how two rates come to state different populations (#34).

    The delivery payload carries the kit's REPORTING CONTRACT (#126): the outcome
    partition (stated / unstated / invalid) for BOTH run and close rows, the rework
    declaration population, and the two populations a per-success rate needs. Every one
    of them is derived from the canonical trailer through the shared predicates, never
    from prose, and every rate travels beside the coverage that says how much of the
    ledger it speaks for.
    """
    if not ledger_path.is_file():
        return {
            "exists": False,
            "total_events": 0,
            "event_counts": {},
            "closed_tasks": 0,
            "closed_subjects": 0,
            "close_events": 0,
            "intake_tasks": 0,
            "run_events": 0,
            "runs_by_outcome": {},
            "close_rows_by_outcome": {},
            "run_rows_stating_outcome": 0,
            "close_rows_stating_outcome": 0,
            "unstated_run_rows": 0,
            "unstated_close_rows": 0,
            "invalid_outcome_reports": [],
            "rework_declarations_by_bucket": {},
            "close_rows_declaring_rework": 0,
            "rework_declaration_rate": 0.0,
            "invalid_rework_reports": [],
            "first_pass_yield": 1.0,
            "first_pass_yield_population": 0,
            "first_pass_yield_coverage": [0, 0],
            "lead_times_sec": [],
            "avg_lead_time_sec": 0.0,
            "total_cost_usd": 0.0,
            "avg_cost_per_closed_task_usd": None,
            "cost_per_successful_task_usd": None,
            "successful_closed_tasks": 0,
            "total_tokens_in": 0,
            "total_tokens_out": 0,
            "total_turns": 0,
            "out_of_trailer_rows": [],
            "out_of_trailer_count": 0,
            "latest_closed_subject": None,
            "spot_check": None,
        }, set()

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
    out_of_trailer_rows: list[dict[str, Any]] = []

    close_rows_by_outcome: dict[str, int] = {}
    run_rows_stating_outcome = 0
    close_rows_stating_outcome = 0
    invalid_outcome_reports: list[str] = []
    # The close trailer's `rework` marker — the disposition a close DECLARES (#106,
    # ruling `n=386` clause 5). Read through the ONE predicate, `declared_rework`.
    rework_declarations_by_bucket: dict[str, int] = {}
    close_rows_declaring_rework = 0
    invalid_rework_reports: list[str] = []
    successful_closed_subjects: set[str] = set()
    close_events = 0

    for ev in events:
        ev_type = ev.get("event", "unknown")
        event_counts[ev_type] = event_counts.get(ev_type, 0) + 1
        subj = ev.get("subject", "")
        ts = ev.get("ts", "")
        detail = ev.get("detail", "")

        # THE TRAILER SCOPE (#498, #34). Telemetry is read from the CANONICAL TRAILER,
        # never from the whole detail: a `cost_usd=` quoted in a sentence is a MENTION of
        # another row's figure, and aggregating it double-counts a row that already
        # declared it. This was the last field reader in the tree that scanned text
        # itself rather than composing the shared predicate. Measured at 3435 rows, 61
        # diverge from the trailer read -- in BOTH directions, including a row read as
        # `failed` off prose quoting a different row's trailer.
        declared = declared_telemetry(detail)
        for key, value in declared:
            try:
                if key in ("cost_usd", "cost"):
                    total_cost_usd += float(value.rstrip("$"))
                elif key in ("tokens_in", "in_tokens"):
                    total_tokens_in += int(value)
                elif key in ("tokens_out", "out_tokens"):
                    total_tokens_out += int(value)
                elif key == "turns":
                    total_turns += int(value)
            except ValueError:
                pass

        # VISIBILITY ONLY: the rows whose telemetry-shaped tokens sit OUTSIDE the trailer.
        # They are NOT aggregated -- that is the whole point -- but a reader must be able
        # to see what the trailer scope set aside, and where. A multiset difference, so a
        # token both declared and merely mentioned still reports the mention.
        leftover = Counter(f"{k}={v}" for k, v in mentioned_telemetry(detail)) - Counter(
            f"{k}={v}" for k, v in declared
        )
        if leftover:
            out_of_trailer_rows.append(
                {"n": ev.get("n"), "event": ev_type, "tokens": sorted(leftover)}
            )

        if ev_type == "intake" and subj and subj not in subjects_intake:
            subjects_intake[subj] = ts
        elif ev_type == "claim" and subj and subj not in subjects_claim:
            subjects_claim[subj] = ts
        elif ev_type == "close" and subj:
            subjects_close[subj] = ts
            close_events += 1
            close_outcome, close_problem = declared_outcome(detail)
            close_rows_by_outcome[close_outcome] = close_rows_by_outcome.get(close_outcome, 0) + 1
            if close_problem:
                invalid_outcome_reports.append(f"n={ev.get('n')} close {subj}: {close_problem}")
            if close_outcome in OUTCOME_DOMAIN:
                close_rows_stating_outcome += 1
            if close_outcome == "accepted":
                successful_closed_subjects.add(subj)
            # One ROW declares one disposition, so the row count is taken once per
            # row however many tokens the run carries (the law gives a row one; the
            # list is what lets the resolving gate see every token it must resolve).
            row_declares = False
            for rework_value in declared_rework(detail):
                bucket = rework_bucket(rework_value)
                rework_declarations_by_bucket[bucket] = (
                    rework_declarations_by_bucket.get(bucket, 0) + 1
                )
                if bucket in ("work_unit", "none"):
                    row_declares = True
                elif bucket.startswith("invalid:"):
                    invalid_rework_reports.append(
                        f"n={ev.get('n')} close {subj}: rework value {rework_value!r} "
                        f"is not a work-unit reference (#N) or 'none'"
                    )
            if row_declares:
                close_rows_declaring_rework += 1

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
            outcome, outcome_problem = declared_outcome(detail)
            runs_by_outcome[outcome] = runs_by_outcome.get(outcome, 0) + 1
            if outcome_problem:
                invalid_outcome_reports.append(f"n={ev.get('n')} run {subj}: {outcome_problem}")
            if outcome in OUTCOME_DOMAIN:
                run_rows_stating_outcome += 1

    # First-pass yield: accepted runs ÷ the run rows that STATE an outcome.
    #
    # Not ÷ every run row. A row declaring no outcome is UNKNOWN, not a success
    # (#53, ruling n=333 clause 2): counting it as accepted is the defect, and
    # counting it as a failure would be a second fabrication in the other
    # direction. So it is excluded from the ratio and COUNTED BESIDE IT — the
    # printed coverage is what tells a reader how much of the ledger the number
    # speaks for (clause 5). No gate rides on the coverage (clause 6): against
    # append-only historical rows a threshold would be permanently RED with no
    # lawful repair, the empty-repair-space defect ruled at n=328.
    #
    # With runs present and not one stating an outcome the ratio is UNDEFINED, and
    # 1.0 would be exactly the favourable fabrication this clause removes.
    unstated_run_rows = runs_by_outcome.get("unstated", 0)
    unstated_close_rows = close_rows_by_outcome.get("unstated", 0)
    if run_rows_stating_outcome > 0:
        yield_val: float | None = (
            runs_by_outcome.get("accepted", 0) / run_rows_stating_outcome
        )
    elif total_runs > 0:
        yield_val = None
    else:
        yield_val = 1.0

    avg_lead_time = (sum(lead_times_sec) / len(lead_times_sec)) if lead_times_sec else 0.0
    # The work unit is a distinct closed SUBJECT, never a close ROW: a subject that is
    # re-opened and closed again carries two close rows, and counting rows would inflate
    # every per-task denominator. `close_events` travels beside it so the two are
    # reconcilable rather than silently disagreeing.
    closed_count = len(subjects_close)
    successful_closed_tasks = len(successful_closed_subjects)
    # An UNDEFINED quotient is `None`, never `0.0` (#620): a zero denominator publishes
    # `$0.0000`, which reads as "this costs nothing" — the flattering value for a state
    # nobody measured. `None` travels to the formatter as `n/a`.
    avg_cost_per_closed_task = (total_cost_usd / closed_count) if closed_count > 0 else None
    cost_per_successful_task = (
        total_cost_usd / successful_closed_tasks if successful_closed_tasks > 0 else None
    )

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
        "closed_subjects": closed_count,
        "close_events": close_events,
        "intake_tasks": len(subjects_intake),
        "run_events": total_runs,
        "runs_by_outcome": runs_by_outcome,
        "close_rows_by_outcome": close_rows_by_outcome,
        "run_rows_stating_outcome": run_rows_stating_outcome,
        "close_rows_stating_outcome": close_rows_stating_outcome,
        "unstated_run_rows": unstated_run_rows,
        "unstated_close_rows": unstated_close_rows,
        "invalid_outcome_reports": invalid_outcome_reports,
        "rework_declarations_by_bucket": rework_declarations_by_bucket,
        "close_rows_declaring_rework": close_rows_declaring_rework,
        "rework_declaration_rate": round(
            close_rows_declaring_rework / close_events if close_events > 0 else 0.0, 4
        ),
        "invalid_rework_reports": invalid_rework_reports,
        # The RAW ratio, never pre-rounded (#143). A 4 dp pre-round here was the second
        # site that made the disagreement: `int(round(r,4)*100)` and `round(round(r,4)*100,1)`
        # are two operations on two different numbers. One rounding, in the formatter.
        "first_pass_yield": yield_val,
        "first_pass_yield_population": run_rows_stating_outcome,
        "first_pass_yield_coverage": [run_rows_stating_outcome, total_runs],
        "lead_times_sec": lead_times_sec,
        "avg_lead_time_sec": round(avg_lead_time, 1),
        "total_cost_usd": round(total_cost_usd, 4),
        "avg_cost_per_closed_task_usd": (
            None if avg_cost_per_closed_task is None else round(avg_cost_per_closed_task, 4)),
        "cost_per_successful_task_usd": (
            None if cost_per_successful_task is None else round(cost_per_successful_task, 4)),
        "successful_closed_tasks": successful_closed_tasks,
        "total_tokens_in": total_tokens_in,
        "total_tokens_out": total_tokens_out,
        "total_turns": total_turns,
        "out_of_trailer_rows": out_of_trailer_rows,
        "out_of_trailer_count": len(out_of_trailer_rows),
        "latest_closed_subject": latest_closed,
        "spot_check": spot_check,
    }, set(subjects_close)


def parse_rework(rework_path: Path, closed_subject_set: set[str]) -> dict[str, Any]:
    """Parse rework.md for defects, unprevented items, and both rework rates.

    Two forms are returned, each named with its own denominator, because two
    different numbers travel under the name "rework rate" and the wrong one gets
    quoted:

      * rework_share     — entries ÷ (closed subjects + entries): how much of the
                           work done was repair. The form comparable across factories.
      * rework_per_close — entries ÷ closed subjects: repair paid per unit of
                           planned work.

    The denominator is distinct closed SUBJECTS, never close rows: a subject
    re-closed after a re-open must not inflate it. It arrives as the ledger's own
    closed-subject SET rather than as a count, so the population cannot drift between
    this function and its caller.

    A third metric is returned: **change fail rate** (O2 Stability) — closes that
    produced a rework entry ÷ closed work units — with its linkage coverage. The
    numerator is DERIVED from the `Subject` column, never stated: an entry counts only
    when its Subject names a work unit that actually closed, so a `#N` pointing at
    something that never landed is not a failed change. Coverage is mandatory and
    travels in the same payload, because an under-linked numerator of 0 reads as
    "nothing ever failed" when the truth is "nothing is linked".
    """
    closed_subjects = len(closed_subject_set)
    if not rework_path.is_file():
        return {
            "exists": False,
            "total_entries": 0,
            "unprevented_entries": 0,
            "closed_subjects": closed_subjects,
            "rework_share": 0.0,
            "rework_per_close": 0.0,
            "change_fail_rate": 0.0,
            "change_fail_rate_numerator": 0,
            "change_fail_rate_denominator": closed_subjects,
            "subject_coverage": 0.0,
            "subject_coverage_numerator": 0,
            "subject_coverage_denominator": 0,
        }

    entries = 0
    unprevented = 0
    determinate = 0
    change_failures = 0
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
                    subject_col = parts[7] if len(parts) > 7 else ""
                    if SUBJECT_WORK_UNIT_RE.fullmatch(subject_col):
                        determinate += 1
                        if subject_col in closed_subject_set:
                            change_failures += 1
                    elif subject_col == SUBJECT_NONE:
                        # Not attributable to a closed work unit: determinate, not a failure.
                        determinate += 1

    rework_share = (entries / (closed_subjects + entries)) if (closed_subjects + entries) > 0 else 0.0
    rework_per_close = (entries / closed_subjects) if closed_subjects > 0 else 0.0
    change_fail_rate = (change_failures / closed_subjects) if closed_subjects > 0 else 0.0
    subject_coverage = (determinate / entries) if entries > 0 else 0.0

    return {
        "exists": True,
        "total_entries": entries,
        "unprevented_entries": unprevented,
        "closed_subjects": closed_subjects,
        "rework_share": round(rework_share, 4),
        "rework_per_close": round(rework_per_close, 4),
        "change_fail_rate": round(change_fail_rate, 4),
        "change_fail_rate_numerator": change_failures,
        "change_fail_rate_denominator": closed_subjects,
        "subject_coverage": round(subject_coverage, 4),
        "subject_coverage_numerator": determinate,
        "subject_coverage_denominator": entries,
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
    # REGIME: the 87s basis below is UNLOADED (2026-09-29). RE-MEASURED in the co-tenant
    # LOADED regime on 2026-10-02 (#72's regime), n=4 whole-command samples, all rc=0:
    # 113.58 / 115.58 / 124.12 / 125.43s at load 3.82-6.55 on 4 cores. The audit's own run
    # that day corroborates it at 119.8s. So the loaded worst is 1.4417x the unloaded basis,
    # and 350.0 is a 2.7904x factor on the LOADED worst -- NOT the 4.0x the paragraph below
    # claims of its 87s basis, which is stale for the regime the audit actually runs in.
    # NOT re-derived, deliberately: the manifest's re-derivation trigger is a CONTAINMENT
    # EVENT (a sample exhausting the budget and the audit reporting UNKNOWN), and this gate
    # has never had one -- the wall still contains the observed loaded worst with 224.57s
    # (2.79x) of headroom. Inflating a wall that has never killed a passing gate would buy
    # 2.5 extra minutes of tolerance for a genuinely hung gate and pay for it in signal.
    # RE-DERIVATION IS OWED at the first UNKNOWN this gate reports, and the basis is then
    # 125.43s in this regime (law: margin_x 4.006 -> 502.47s), never the 350.0 itself (#226).
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
    # the last one (#226).
    # THE REGIME GAP IS NAMED, NOT HIDDEN. These 12 samples were taken at load 4.51-7.87,
    # while the incident that filed #72 happened at 5.54 / 11.10 / 13.15 on 4 cores with io
    # full avg300=4.85 -- a HEAVIER regime than any sample here, so this basis is a FLOOR on
    # the regime that bit, not a reading of it. Two things cover the gap. First, the one
    # observation we do have from that heavier regime is the kill itself: the gate ran past
    # 70.17s, so its true runtime exceeded 70.17s -- 2.3328x this worst sample -- and the new
    # wall contains that observed lower bound with 1.7254x headroom, where the old 70.0 sat
    # 0.17s BELOW the kill it produced. Second, #72's condition 1 is what makes the residual
    # safe: a co-tenant day heavier than anything sampled here can still exhaust this wall,
    # and when it does the audit reports UNKNOWN and takes NO verdict, instead of the false
    # DEGRADED that filed this item.
    "tests/test_cron_thinness.py": 121.07,
    # REGIME: co-tenant LOADED (2026-10-02, the same regime #72 calibrated in), because a
    # budget must be sized in the regime it runs in and this is the regime the audit runs
    # in. measured 0.21 / 0.23 / 0.24 / 0.28 / 0.28 / 0.33 / 0.36 / 1.00s wall over eight
    # whole-command samples at load 5.24-5.26 on 4 cores (script-mode, stdlib only, so the
    # cost is interpreter startup; the 1.00s head is the cold first sample). Worst observed
    # 1.00s -> margin_x 4.75 -> 4.75, rounded UP to 5.0, i.e. a 5.0x factor on the worst
    # observed and still far below the 30.0 default. An earlier UNLOADED reading of
    # 0.66 / 0.33 / 0.31s (three runs) is kept only as the comparison: its head is 1.5x
    # smaller, so the loaded regime is the one that sizes this wall.
    "tests/test_ledger_commit_cites_no_rows.py": 5.0,
    # REGIME: co-tenant LOADED. measured 9.97 / 9.55 / 10.23 / 13.14 / 9.67 / 11.45 /
    # 12.71 / 11.51 / 12.83s wall over nine runs on 2026-09-30-10-01, the later ones taken
    # at load 11.49 on 4 cores (co-tenant load, board item #72) -- so the basis already sits
    # in the loaded regime and needs no re-derivation. The cost is NOT startup alone: ~6.0s
    # is the guard's own deliberate sleeps (a 1.0s and a 5.0s staged gate, load-insensitive
    # by construction), the rest is interpreter startup plus two driven `audit.py --stamp`
    # subprocesses. Derivation from the worst observed: 12.83s -> margin_x 4.0585 -> 52.07,
    # rounded UP to 53.0 -- the house 4x precedent, stated as arithmetic rather than asserted.
    # Corroborated 2026-10-02 at 9.15s inside the #72 leg-1 audit (load 4.78, 4 cores).
    # The spread is load, not the guard: the fixed sleeps put a floor under the runtime and
    # a ceiling on how far contention can stretch it.
    "tests/test_audit_stamp_duration.py": 53.0,
    # The questions-store selftest drives EVERY verb of tools/questions and finishes with a
    # `node render.mjs` build, so it is minutes, not seconds -- the kit's own budget for this
    # same gate is 3373.43s from an 843.17s basis (margin_x 4.001), which is the corroboration
    # that a 10-minute wall here is the gate's NORMAL shape and not a hang. Measured on this
    # box 2026-11-05 under /usr/bin/time, n=1, co-tenant loaded regime: WALL=633.89s, rc=0.
    # Derivation: 633.89 * (4.0 + 0.75/633.89) = 2536.31, rounded UP to the whole second per
    # the 12.83s->53.0 precedent above -> 2537.0 (a 4.0013x factor on the measurement).
    # REGIME NOTE: n=1. This gate is registered only so an unregistered gate cannot hide; its
    # wall is deliberately generous because a timeout here reports a PASSING gate as a
    # failure, which is the exact defect the 100.12s entry above was corrected for.
    "tests/test_questions.py": 2537.0,
}

# PYTEST-FORM WALLS (#107). These nine gates have no `__main__`, so they run ONLY under
# `-m pytest` — and the interpreter + plugin startup dominates their wall by an order of
# magnitude over their own work. Measured 2026-11-05 on this box, whole-command wall vs
# pytest's own reported time, n=1 each, co-tenant loaded regime:
#
#   test_binding_mechanism_exists.py     37.5s wall /  7.42s work
#   test_close_telemetry_provenance.py   30.6s wall /  0.39s work
#   test_hygiene_inflight.py             40.6s wall /  4.89s work
#   test_hygiene_namespace.py            37.5s wall /  5.35s work
#   test_insights_gate_recorded.py       26.5s wall /  0.59s work
#   test_ledger_close_preflight.py       35.4s wall /  7.09s work
#   test_registry_attest.py              29.5s wall /  0.24s work
#   test_registry_render.py              22.9s wall /  0.16s work
#   test_score_gate_recorded.py          30.5s wall /  0.40s work
#   test_audit_rates.py                  22.08s wall / 4.19s work   (#126, n=3 below)
#
# test_audit_rates.py re-measured 2026-11-05 in the same loaded regime (1-min load 7.61 on 4
# cores), n=3 whole-command samples, all rc=0: 22.08 / 20.27 / 18.61s wall against 4.19 / 4.37
# / 4.48s of pytest-reported work. Worst 22.08s; the manifest's margin law is 4.0 + 0.75/22.08
# = 4.034x -> 89.07s, declared 90.0 (4.08x the worst). Rounding DOWN to 85.0, the way 162.4
# became 150.0, would sit BELOW the 4.0x margin the same law names, so this one rounds to the
# nearest round second ABOVE it. Its own work is ~4s; the wall is interpreter + plugin startup,
# which is why the bare DEFAULT_GATE_WALL_SEC of 30.0 would leave only 1.36x on the worst.
#
# FOUR MORE, PORTED WITH THE HYGIENE FAMILY (#107, measured 2026-11-11, whole-command wall
# vs pytest's own reported time, n=1 each, same loaded regime):
#
#   test_hygiene_build_residue.py        17.95s wall /  0.80s work
#   test_hygiene_declaration_sweep.py    16.70s wall /  0.97s work
#   test_hygiene_stale_dirs.py           13.76s wall /  0.67s work
#   test_hygiene_branch_occupancy.py     13.54s wall /  0.10s work
#
# These four DO carry a `__main__`, unlike the nine above — so the group's predicate is not
# "no `__main__`" but "no CHEAPER form exists", which is the fact that actually decides the
# registration. Each `__main__` is three lines ending in
# `sys.exit(pytest.main([__file__, "-q"]))`, i.e. the bare-script form pays the SAME pytest
# interpreter + plugin startup and adds one import on top. So they take the group's declared
# wall rather than a bare-script one, and the group's slowest sample (40.6s) is unchanged by
# their arrival, which is what 150.0 was derived from.
#
# The DEFAULT_GATE_WALL_SEC of 30.0 would have killed six of the nine on the FIRST run, and
# killed them into the worst shape: run_gate reports a timeout with duration 0.0 and the
# exception in stderr, which is indistinguishable from an instant logic error. The declared
# wall is the house 4x-of-worst precedent applied to the slowest measurement (40.6s -> 162.4)
# and rounded DOWN to a round 150.0, which is 3.69x that worst and 4.0x the next one. Stated
# as arithmetic rather than picked by feel (ai-antispam#64, #72).
PYTEST_FORM_GATES: tuple[str, ...] = (
    "tests/test_audit_rates.py",
    "tests/test_binding_mechanism_exists.py",
    "tests/test_close_telemetry_provenance.py",
    "tests/test_hygiene_branch_occupancy.py",
    "tests/test_hygiene_build_residue.py",
    "tests/test_hygiene_declaration_sweep.py",
    "tests/test_hygiene_inflight.py",
    "tests/test_hygiene_namespace.py",
    "tests/test_hygiene_stale_dirs.py",
    "tests/test_insights_gate_recorded.py",
    "tests/test_ledger_close_preflight.py",
    "tests/test_registry_attest.py",
    "tests/test_registry_render.py",
    "tests/test_score_gate_recorded.py",
    "tests/test_self_audit_push_gate.py",
)
for _pytest_gate in PYTEST_FORM_GATES:
    GATE_WALLS_SEC[_pytest_gate] = 150.0
# ...except the one measured on its own in the table above: its wall is 90.0, not the group's
# 150.0, because its worst sample is 22.08s rather than the 40.6s the group's 150.0 was derived
# from. Stated here rather than folded into the loop so the two numbers keep their own arithmetic.
GATE_WALLS_SEC["tests/test_audit_rates.py"] = 90.0


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
                # #552 (ruling n=4167): a gate that CHOOSES the kill code exited NORMALLY and
                # was recorded `unknown: False`, so the audit counted it FAILED while the
                # gate-side split read the same run as NO VERDICT -- one run described two
                # ways by two surfaces. The kill code IS the gate's own statement that it
                # took no verdict, so it is UNKNOWN here too. The discriminating arm is a
                # genuine non-zero exit (exit 3), which stays FAILED: the change cannot pass
                # by calling every non-zero exit unknown.
                "unknown": res.returncode == TIMEOUT_EXIT_CODE,
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

# How STRONGLY one stream DECLARES its cause, ranked (kit #533). The rank is a property of
# what the text CONTAINS, never of its length or its position, so an advisory block printed
# before the real summary cannot promote a stream.
GATE_CAUSE_STREAM_EMPTY = -1
GATE_CAUSE_STREAM_PROSE = 0
GATE_CAUSE_STREAM_COUNT = 1
GATE_CAUSE_STREAM_DECLARED = 2

def gate_cause_stream_rank(text: str) -> int:
    """How STRONGLY one stream DECLARES its cause: empty < prose < count line < marker."""
    if not text or not text.strip():
        return GATE_CAUSE_STREAM_EMPTY
    rank = GATE_CAUSE_STREAM_PROSE
    for line in text.splitlines():
        if not line.strip():
            continue
        if declared_cause_count(line) is not None:
            return GATE_CAUSE_STREAM_DECLARED
        if _COUNT_FIRST_RE.search(line):
            rank = GATE_CAUSE_STREAM_COUNT
    return rank

def gate_cause_from_streams(stdout: str | None, stderr: str | None) -> str:
    """THE STREAM JOIN, ranked by what each stream declares (kit #533).

    #205 settled WHICH line inside one text is the cause: the tool's own declared marker
    first, the count-line heuristic next, the last line last. The join ACROSS the two
    streams then threw that precedence away: `reported_cause(stdout) or reported_cause(stderr)`
    returns a non-empty string for ANY non-empty text (its last line at minimum), so a
    non-empty stdout always won and stderr was never read. A gate that prints prose to
    stdout and declares its count on stderr therefore recorded an INFORMATIONAL line as the
    cause — measured on `tools/hygiene.py --audit --require-committed`, which writes its
    supersession advisories to stdout and `oc-cause-count: 1` to stderr, so the note named a
    different evidence file and dropped the count.

    So the rank travels ACROSS the boundary, which is #205's rule applied one layer up: take
    the higher-ranked stream, fall back to the other when one is empty, and when NEITHER
    declares (both prose, or one empty) keep `stdout or stderr` so a tool that has not
    adopted the marker reads byte-identically to before.

    A tie keeps stdout, deliberately: equal rank means neither stream has a better claim, and
    preserving the historical winner is the additive half of the change — with ONE exception.

    WHEN BOTH STREAMS DECLARE AND THE COUNTS DISAGREE, the note names BOTH (kit HQ ruling
    2026-10-21T12). Two streams carrying different totals is itself the finding: silently
    keeping stdout would report one gate's failure count as if it were the other's, and the
    disagreement — the only thing that says the two halves of one gate disagree about what
    happened — would be the single fact the note omitted. Both counts are named BEFORE the
    specimen text, so the bound can truncate a specimen but never a count.
    """
    out_text = stdout or ""
    err_text = stderr or ""
    out_rank = gate_cause_stream_rank(out_text)
    err_rank = gate_cause_stream_rank(err_text)
    if out_rank == GATE_CAUSE_STREAM_DECLARED and err_rank == GATE_CAUSE_STREAM_DECLARED:
        out_count = declared_cause_count(out_text)
        err_count = declared_cause_count(err_text)
        if out_count is not None and err_count is not None and out_count != err_count:
            return (
                f"counts DISAGREE -- stdout {out_count}, stderr {err_count}: "
                f"{reported_cause(out_text)} | {reported_cause(err_text)}"
            )[:GATE_CAUSE_LIMIT]
    if err_rank > out_rank:
        return reported_cause(err_text)
    return reported_cause(out_text) or reported_cause(err_text)

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
        g["note"] = gate_cause_from_streams(g.get("stdout"), g.get("stderr"))

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

def attach_gate_staleness(gate_results: list[dict], stale) -> None:
    """Set `stale_basis` on every gate whose declared basis no longer describes it (#231).

    THE JOIN, at the report layer and nowhere else. The staleness sweep and the gate
    verdicts are computed in the same run but in different scopes, and they are printed
    apart -- so a reader meeting a kill sees ONE word, UNKNOWN, for TWO different questions:
    "is the cap a valid load bound?" (#226) and "does the basis describe a DIFFERENT test?"
    (#231). Joining them here is what lets the verdict LINE say which question the kill is
    evidence for.

    It computes nothing. Both operands already exist, and it touches NO value -- the values
    are the process owner's, never the implementing lane's (n=574 PART 5). The staleness
    sweep REPORTS and never gates; so does this join, and for the same reason: a basis whose
    bytes moved can be LOOSER than reality, so acting on it automatically would tighten a
    cap that was containing its gate.
    """
    by_key = {s.key: s for s in (stale or ())}
    for g in gate_results:
        s = by_key.get(g.get("gate_key"))
        if s is None:
            g.pop("stale_basis", None)
            continue
        g["stale_basis"] = {"legs": list(s.legs), "detail": s.detail}

def format_stale_basis_mark(g: dict) -> str:
    """The staleness suffix for one gate's line, or "" where its basis still describes it.

    Both legs are named (`[STALE:bytes]`, `[STALE:bytes+runner]`) and the evidence carries
    the two blobs, so the reader sees WHICH comparison moved rather than a bare warning.
    """
    s = g.get("stale_basis")
    if not s:
        return ""
    legs = "+".join(s.get("legs") or [])
    detail = (s.get("detail") or "").replace("|", "/")
    return f" [STALE:{legs}] {detail}" if detail else f" [STALE:{legs}]"

def stale_killed_gates(gate_results: list[dict]) -> list[dict]:
    """The KILLED/UNKNOWN gates whose declared basis is stale -- the join's own population."""
    return [g for g in gate_results if g.get("unknown") and g.get("stale_basis")]

def render_tree_line(tree: dict[str, Any]) -> str:
    """The provenance line beside the verdict (#150 criterion 2).

    `DEGRADED` must never be readable without the tree it was read on: the measured
    instance was a verdict that named a peer's half-written file, which a reader took as
    a statement about HEAD. The count comes FIRST because it is the fact that decides
    whether the failure list describes the repository or a lane's mid-turn edit, and the
    paths follow so the reader can attribute without a second `git status`.
    """
    sha = tree.get("head_sha")
    sha_txt = sha[:9] if sha else "UNREADABLE"
    count = tree.get("modified_count", 0)
    if not sha:
        # NO COUNT HERE, and the omission is the point: git could not answer, so the
        # modification count is UNKNOWN rather than zero. Printing "0 modified tracked
        # path(s)" would assert the strongest provenance claim -- a clean tree -- on the
        # weakest evidence, which is the false clean this whole field exists to prevent.
        return ("  - Tree read: HEAD UNREADABLE, modification count UNKNOWN "
                "\u2014 the verdict names no revision, so it is not a claim about any")
    if count:
        return (f"  - Tree read: HEAD {sha_txt} with {count} modified tracked path(s) "
                f"\u2014 the verdict describes THIS tree, not necessarily HEAD: "
                + ", ".join(tree.get("modified_paths", [])))
    return (f"  - Tree read: HEAD {sha_txt}, 0 modified tracked path(s) "
            f"\u2014 the verdict describes HEAD")

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
    # The basis join, on the SAME line (#231): a kill under a stale basis and a kill under a
    # sound one are the same UNKNOWN, so the staleness the sweep already computed is printed
    # beside the verdict it bears on. Appended AFTER the `({dur}s)` token for the reason
    # stated above -- the stamp-duration gate parses that token and must keep matching.
    line += format_stale_basis_mark(g)
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

    # 3b. Rework relative-revision gate (#129). It was DEFERRED in
    # `registry/gate-run-deferrals.json` behind one reason shared with its sibling —
    # "the evidence format this tree uses has no `Subject` column" — and that blocker
    # died on 2026-11-05 when #126 added the column. Behind the stale reason the gate
    # was not merely unregistered, it CRASHED: it read the Subject cell through
    # `COLUMNS.index("Subject")`, and `COLUMNS` deliberately omits that field, so the
    # live table raised `ValueError: list.index(x): x not in list`. Registered here only
    # after it ran to a verdict (exit 0) on the repaired read, because a registration
    # while red turns the daily audit red. SCRIPT form: it is a `check(...)` script, so
    # pytest would report "no tests ran" and exit 0 — a green that measured nothing.
    if (repo_root / "tests/test_rework_relative_revision.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_rework_relative_revision.py"])

    # 3c. Rework declared-to-landed gate (#129). Its deferral named an EXPIRED blocker —
    # "the evidence format this tree uses has no `Subject` column" — which #126 removed on
    # 2026-11-05. Behind it sat a second, real reason the entry never said: the gate's
    # vacuity leg refuses to report green while NO close row declares `rework=#N`, so
    # registering it before such a row existed would have turned the daily audit red with
    # "the gate examined nothing". The write path now PRODUCES and REQUIRES that
    # declaration (05766a4), and close n=309 is the first row appended under it, resolving
    # to the #129 entry in `evidence/rework.md`. Registered only after a measured green:
    # 1 declaration in scope, resolved, exit 0. SCRIPT form — a `check(...)` script, so
    # pytest would report "no tests ran" and exit 0, a green that measured nothing.
    if (repo_root / "tests/test_rework_declared_landed.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_rework_declared_landed.py"])

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

    # 17. The cadence record (#116) -- the cadence leg must NAME the record it reads and tell a
    # DROPPED ROW (the run happened; its handoff row never landed) from a MISSED RUN. Measured
    # 2026-10-03: the scorecard `dd16280` was committed, the owed `run` row was dropped, and the
    # report read `MISSED: Last run: 39.9h ago`. Those two failures have OPPOSITE remedies --
    # re-stamp the row vs. fix the pacemaker -- so a leg that cannot separate them sends the
    # reader to the wrong file, and a dropped handoff on a day the audit already reds for
    # another reason is invisible entirely.
    #
    # SCRIPT-form, matching #16: `main()` under a `__main__` guard, no module-level `def test_*`.
    # It imports the tool and calls the pure functions with fixture paths, so it never touches
    # live state and runs in well under a second.
    if (repo_root / "tests/test_audit_cadence_record.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_audit_cadence_record.py"])

    # 18. The cadence SUBJECT binding (#124) -- #17 above tells a DROPPED ROW from a MISSED RUN,
    # but it read the clock off ANY `run`/`score` row, so an unrelated ledger row landing on a
    # quiet day moved the clock and the leg read HELD while the self-audit had not run at all.
    # Measured 2026-10-27: the day's audit never ran, and `n=267` -- a `#123` correction row --
    # satisfied the leg's staleness check; the report read HELD. The clock is now bound to
    # `subject == internal-self-audit`, and this gate reds if that binding is removed.
    #
    # SCRIPT-form, matching #16/#17.
    if (repo_root / "tests/test_audit_cadence_subject.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_audit_cadence_subject.py"])

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

    # ---------------------------------------------------------------------------------
    # THE DECLARED-GATE SET (#107). Before this block, 38 files declared themselves gates
    # and 37 were never registered, so they never ran — and the registration gate could
    # only report that as ONE truncated line (it truncates at 90 characters, the n=571
    # exempt-by-silence class). Each entry below is guarded by `is_file()` so a
    # bootstrapped factory runs the subset it ships, and each was run to a verdict BEFORE
    # registration: a gate registered while red turns the daily audit red, which is why the
    # set is the PASSING set and the rest are declared on their own surface.
    #
    # THE FORM IS NOT COSMETIC. Nine of these have no `__main__`, so `[sys.executable, path]`
    # would exit 0 having executed ZERO checks — a decorative green, the exact class gate 10
    # exists for. They are registered under `-m pytest` with the wall measured above. This is
    # also why `test_ledger_close_preflight.py` is now registered in pytest form: the note
    # that stood here refused it on three grounds, and two of the three are gone — the wall
    # is DECLARED (150.0s against 35.4s measured) rather than the hardcoded 30.0s, and
    # running it twice (CI and here) is not a defect but the cost of a gate that actually
    # runs in the surface that reports. The third ground stands and is why the form matters.
    # ---------------------------------------------------------------------------------
    # script form — each carries its own `__main__` and returns a real exit code
    if (repo_root / "tests/test_audit_cadence_own_run.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_audit_cadence_own_run.py"])
    if (repo_root / "tests/test_audit_tree_condition.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_audit_tree_condition.py"])
    if (repo_root / "tests/test_board_intake_recorded.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_board_intake_recorded.py"])
    if (repo_root / "tests/test_brain_metrics.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_brain_metrics.py"])
    if (repo_root / "tests/test_close_row_revision.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_close_row_revision.py"])
    if (repo_root / "tests/test_commit_close_keyword.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_commit_close_keyword.py"])
    if (repo_root / "tests/test_commit_pair_hook.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_commit_pair_hook.py"])
    if (repo_root / "tests/test_commit_session_trailer.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_commit_session_trailer.py"])
    if (repo_root / "tests/test_criteria_count.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_criteria_count.py"])
    if (repo_root / "tests/test_docs_sync.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_docs_sync.py"])
    if (repo_root / "tests/test_duplicate_prose.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_duplicate_prose.py"])
    if (repo_root / "tests/test_gate_fixtures_closure.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_gate_fixtures_closure.py"])
    if (repo_root / "tests/test_gate_invocation_mode.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_gate_invocation_mode.py"])
    if (repo_root / "tests/test_gate_registration.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_gate_registration.py"])
    if (repo_root / "tests/test_ledger_identity.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_ledger_identity.py"])
    if (repo_root / "tests/test_ledger_index.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_ledger_index.py"])
    if (repo_root / "tests/test_ledger_no_shrink.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_ledger_no_shrink.py"])
    if (repo_root / "tests/test_publish.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_publish.py"])
    # Registered under #107: the questions store's own selftest. It drives every verb and
    # the renderer, so its wall is declared in GATE_WALLS_SEC from a MEASURED runtime —
    # the 120s fallback would kill a healthy gate and report UNKNOWN.
    if (repo_root / "tests/test_questions.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_questions.py"])
    if (repo_root / "tests/test_registry.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_registry.py"])
    if (repo_root / "tests/test_roadmap_transition.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_roadmap_transition.py"])
    if (repo_root / "tests/test_skill_version_contract.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_skill_version_contract.py"])
    if (repo_root / "tests/test_subject_anchor.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_subject_anchor.py"])
    if (repo_root / "tests/test_template_integrity.py").is_file():
        gates_to_run.append([sys.executable, "tests/test_template_integrity.py"])

    # pytest form — no `__main__`, so a bare-script registration would execute
    # ZERO checks and exit 0. The wall is declared in GATE_WALLS_SEC above.
    if (repo_root / "tests/test_binding_mechanism_exists.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_binding_mechanism_exists.py", "-q"])
    if (repo_root / "tests/test_close_telemetry_provenance.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_close_telemetry_provenance.py", "-q"])
    if (repo_root / "tests/test_hygiene_inflight.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_hygiene_inflight.py", "-q"])
    if (repo_root / "tests/test_hygiene_namespace.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_hygiene_namespace.py", "-q"])
    if (repo_root / "tests/test_hygiene_build_residue.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_hygiene_build_residue.py", "-q"])
    if (repo_root / "tests/test_hygiene_declaration_sweep.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_hygiene_declaration_sweep.py", "-q"])
    if (repo_root / "tests/test_hygiene_stale_dirs.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_hygiene_stale_dirs.py", "-q"])
    if (repo_root / "tests/test_hygiene_branch_occupancy.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_hygiene_branch_occupancy.py", "-q"])
    if (repo_root / "tests/test_insights_gate_recorded.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_insights_gate_recorded.py", "-q"])
    if (repo_root / "tests/test_ledger_close_preflight.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_ledger_close_preflight.py", "-q"])
    if (repo_root / "tests/test_registry_attest.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_registry_attest.py", "-q"])
    if (repo_root / "tests/test_registry_render.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_registry_render.py", "-q"])
    if (repo_root / "tests/test_score_gate_recorded.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_score_gate_recorded.py", "-q"])
    # Registered under #135: the daily self-audit's push leg. `git push <remote> main` pushes a
    # REF, not a commit, so an unconditional push in step 3 ships whatever held leg happens to
    # be on main (measured 2026-11-08: 19 commits ahead of origin, all of them the owner-gated
    # #130 roster leg). The gate asserts the procedure names the ahead-count read and both
    # branches, REJECTS the pre-fix step text as a negative control, and proves the push
    # coupling in a throwaway two-remote sandbox. pytest form, so a bare-script registration
    # would execute ZERO checks and exit 0.
    if (repo_root / "tests/test_self_audit_push_gate.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_self_audit_push_gate.py", "-q"])
    # Registered under #126: the reporting contract's own gate -- both rework rate FORMS
    # with their denominators, the outcome partition for run AND close rows, the rework
    # declaration population, the change fail rate with its linkage coverage, and the
    # cost-per-successful-task rate. It is pytest form (18 test functions, no `__main__`),
    # so a bare-script registration would execute ZERO checks and exit 0.
    if (repo_root / "tests/test_audit_rates.py").is_file():
        gates_to_run.append([sys.executable, "-m", "pytest", "tests/test_audit_rates.py", "-q"])
    results = []
    for cmd in gates_to_run:
        wall = _gate_wall(cmd)
        if wall == DEFAULT_GATE_WALL_SEC:
            print(f"gate budget: {_gate_wall_key(cmd)} uses the declared default {DEFAULT_GATE_WALL_SEC}s")
        results.append(run_gate(cmd, repo_root, budget_sec=wall))
    return results


# The cadence leg reads the LEDGER, which records a run's HANDOFF. The scorecard artifact
# (`evidence/scores/<date>-self-audit.md`) records the run ITSELF. The two can disagree in
# both directions, and a leg reading only the ledger reported a DROPPED ROW as a MISSED RUN
# (#116; measured 2026-10-03: the artifact `dd16280` was committed while the owed row never
# landed, and the report read `MISSED: Last run: 39.9h ago`). So the record is NAMED in the
# output, and a stale ledger row is classified against the artifact series.
LEDGER_RECORD = "evidence/ledger.jsonl run/score rows with subject internal-self-audit"

# The subject that marks a SELF-AUDIT run row. Binding the clock to it is the fix for #124:
# before it, `event in ("run","score")` alone fed the clock, so ANY unrelated board row
# satisfied the daily check. Measured: the `#123` correction row n=267 (2026-10-25T10:21:27Z)
# was the newest `run` row of ANY subject until the 2026-10-28 stamp (n=269) -- 2.81 days --
# and the 2026-10-26 scorecard says so in its own words, `Cadence Status HELD · Last run:
# 19.5h ago`, with verdict PASSED, on a day whose handoff row was never written. Across that
# window TWO rows were dropped (10-25, 10-26) and ONE run never happened (10-27); the clock
# could not tell either from a healthy day while an unrelated row kept it fresh.
SELF_AUDIT_SUBJECT = "internal-self-audit"

# How far back a HOLE is looked for, in days ending at the newest scorecard. Bounded on
# purpose: a hole is proved by an artifact whose row never landed, and the append-only law
# forbids writing that row after the fact -- so an old hole has no repair space and an
# unbounded rule would be a permanent red. Bounded, it names recent debt and ages out as the
# window moves.
CADENCE_HOLE_LOOKBACK_DAYS = 7

# One label per state, shared by the stdout line, the markdown report and the JSON payload
# so three surfaces cannot name the same run differently.
CADENCE_STATE_LABEL = {
    "held": "HELD",
    "missed_run": "MISSED (run)",
    "dropped_row": "DROPPED ROW",
    "hole": "HOLE",
}

def scorecard_dates(scores_dir: Path) -> list[str]:
    """Every dated scorecard artifact as `YYYY-MM-DD`, ascending.

    Only files matching the artifact naming law are read, and each stamp must PARSE as a
    date: a stray file in the directory is not a run's record. `YYYY-MM-DD` sorts
    chronologically, so the list's order is the calendar's.
    """
    if not scores_dir.is_dir():
        return []
    dates = []
    for path in scores_dir.glob("*-self-audit.md"):
        stamp = path.name[: -len("-self-audit.md")]
        try:
            datetime.date.fromisoformat(stamp)
        except ValueError:
            continue
        dates.append(stamp)
    return sorted(dates)

def latest_scorecard_date(scores_dir: Path) -> str | None:
    """The newest dated scorecard artifact as `YYYY-MM-DD`, or None when none parses."""
    dates = scorecard_dates(scores_dir)
    return dates[-1] if dates else None

def find_cadence_holes(
    row_dates: set[str], artifact_dates: list[str], latest_artifact: str | None
) -> list[str]:
    """Days inside the lookback window that carry a scorecard but NO self-audit row.

    An artifact proves the run HAPPENED; a missing row proves the handoff never landed. The
    pre-#124 leg compared only the NEWEST artifact with the last row, so it could name one
    such day and only once the row series had already gone stale -- a hole sitting between two
    healthy rows was invisible (#124 leg A). Here the two series are compared in full.
    """
    if not artifact_dates or not latest_artifact:
        return []
    try:
        newest = datetime.date.fromisoformat(latest_artifact)
    except ValueError:
        return []
    oldest = (newest - datetime.timedelta(days=CADENCE_HOLE_LOOKBACK_DAYS - 1)).isoformat()
    return sorted(d for d in artifact_dates if oldest <= d <= latest_artifact and d not in row_dates)

def render_cadence_holes(holes: list[str]) -> str:
    """The hole days as `first..last`, or a single date when there is only one."""
    if not holes:
        return ""
    if len(holes) == 1:
        return holes[0]
    return f"{holes[0]}..{holes[-1]}"

def classify_stale_cadence(last_run_ts: str, latest_artifact: str | None) -> str:
    """Which record failed: a `dropped_row` (the run happened) or a `missed_run` (it did not).

    A scorecard artifact dated strictly AFTER the last ledger row proves a run happened whose
    handoff never landed, so the ROW was dropped. With no such artifact, nothing recorded a
    run either, so the run itself was missed. Both are real defects and both leave
    `cadence_held` False -- only the NAME differs, and naming it is the point: a dropped
    handoff on a day the audit already reds for another reason is otherwise invisible.
    """
    if latest_artifact is None:
        return "missed_run"
    return "dropped_row" if latest_artifact > last_run_ts[:10] else "missed_run"

def render_cadence_label(stats: dict[str, Any]) -> str:
    """The cadence state's label, for every surface that reports it.

    A `hole` label carries the DAYS it names: a state word alone would tell a reader that
    something is missing without saying which handoffs, which is the same silence the state
    was added to break (#124).
    """
    state = stats.get("state")
    if state is None:
        state = "held" if stats.get("cadence_held") else "missed_run"
    label = CADENCE_STATE_LABEL.get(state, "MISSED")
    holes = stats.get("holes") or []
    if state == "hole" and holes:
        return f"{label} {render_cadence_holes(holes)}"
    return label

def check_cadence_integrity(
    ledger_path: Path,
    scores_dir: Path | None = None,
    own_run_date: str | None = None,
) -> dict[str, Any]:
    """Check whether recurring processes ran according to declared schedule.

    Reads TWO records and says which: `record_read` names the ledger rows the staleness is
    measured from, and `artifact_latest_date` is the newest scorecard on disk -- the value
    that separates a dropped row from a missed run (#116).

    `own_run_date` is the date of the run being computed, and its artifact is EXCLUDED from
    the hole set: the process computing cadence owns that artifact, and the row proving its
    handoff is written after this function returns (or, when a cron reports and a lane
    stamps in a separate invocation, in a later one). Counting it makes a clean day read as
    its own hole -- measured 2026-11-03: `--stamp` on a run whose own report said HELD wrote
    `verdict=DEGRADED`, because the artifact that same report had just written had no row
    yet, and every day since the #124 fix (n>=269) carried the same false red. The day is
    still caught: on the next run it is no longer "own", and the lookback window keeps it
    in scope for the full 7 days.
    """
    latest_artifact = latest_scorecard_date(scores_dir) if scores_dir is not None else None
    artifact_dates = scorecard_dates(scores_dir) if scores_dir is not None else []
    # The exclusion is applied to the HOLE INPUT ONLY -- never to `latest_artifact`. That value
    # is the newest scorecard ON DISK (what this function's docstring promises), it ANCHORS the
    # lookback window in `find_cadence_holes` (rewriting it to yesterday's date silently shortens
    # the window from CADENCE_HOLE_LOOKBACK_DAYS to one day less), and `classify_stale_cadence`
    # compares it against the last row to name `dropped_row` vs `missed_run` (rewriting it turns
    # today's dropped handoff into a "missed run"). Only the hole set needs the run's own day
    # removed, because that is the one comparison that reads the artifact as a missing row.
    hole_dates = [d for d in artifact_dates if d != own_run_date] if own_run_date else artifact_dates
    if not ledger_path.is_file():
        return {
            "cadence_held": False,
            "hours_since_last_run": None,
            "state": "missed_run",
            "record_read": LEDGER_RECORD,
            "artifact_latest_date": latest_artifact,
            "holes": [],
        }

    now = datetime.datetime.now(datetime.timezone.utc)
    last_ts = None
    row_dates: set[str] = set()
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    data = json.loads(line)
                    # #124: the clock is fed ONLY by self-audit rows. An unrelated `run` row
                    # (a board item's correction receipt) is not this process running, and
                    # letting one feed the clock is how three handoffs went unnoticed.
                    if data.get("event") not in ("run", "score"):
                        continue
                    if data.get("subject") != SELF_AUDIT_SUBJECT:
                        continue
                    ts = data.get("ts")
                    if not ts:
                        continue
                    row_dates.add(ts[:10])
                    last_ts = ts
                except Exception:
                    pass

    holes = find_cadence_holes(row_dates, hole_dates, latest_artifact)

    if not last_ts:
        return {
            "cadence_held": True,
            "hours_since_last_run": None,
            "state": "held",
            "record_read": LEDGER_RECORD,
            "artifact_latest_date": latest_artifact,
            "holes": holes,
        }

    try:
        t_last = datetime.datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        hours_diff = (now - t_last).total_seconds() / 3600.0
        # Expected daily cadence: interval <= 30 hours
        cadence_held = hours_diff <= 30.0
        if cadence_held:
            # A hole is invisible to the staleness clock: two healthy rows can bracket a day
            # whose handoff never landed. This is #124 leg A -- the state must name it.
            state = "hole" if holes else "held"
        else:
            state = classify_stale_cadence(last_ts, latest_artifact)
        return {
            "cadence_held": cadence_held and not holes,
            "hours_since_last_run": round(hours_diff, 1),
            "last_run_ts": last_ts,
            "state": state,
            "record_read": LEDGER_RECORD,
            "artifact_latest_date": latest_artifact,
            "holes": holes,
        }
    except Exception:
        return {
            "cadence_held": False,
            "hours_since_last_run": None,
            "state": "missed_run",
            "record_read": LEDGER_RECORD,
            "artifact_latest_date": latest_artifact,
            "holes": holes,
        }


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
    ledger_read_at: str,
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
        f"> **Ledger read at:** `{ledger_read_at}` "
        f"({ledger_stats.get('total_events', 0)} events) — this artifact describes the ledger "
        f"AS OF THAT INSTANT, before this run's own row was appended. It is a START-OF-RUN "
        f"SNAPSHOT, not the ledger's state when you read it: the run row this audit writes "
        f"lands after the read, and concurrent lanes append throughout the gate window "
        f"(#142, ruling n=919).",
        "",
        "---",
        "",
        "## 1. Process Health & Delivery Telemetry",
        "",
        "| Metric | Value | Reference / Derivation |",
        "|---|---|---|",
        f"| **Total Ledger Events** | `{ledger_stats.get('total_events', 0)}` | Continuous ledger sequence |",
        f"| **Closed Tasks** | `{ledger_stats.get('closed_tasks', 0)}` | Distinct closed subjects; `{ledger_stats.get('close_events', 0)}` close rows ({ledger_stats.get('close_events', 0) - ledger_stats.get('closed_tasks', 0)} re-close of a re-opened subject) |",
        f"| **First-Pass Yield** | `{format_yield_rate(ledger_stats)}` | {format_yield_note(ledger_stats)} |",
        f"| **Rework Entries** | `{rework_stats.get('total_entries', 0)}` | Defect count recorded in rework.md |",
        f"| **Rework Declarations** | `{ledger_stats.get('close_rows_declaring_rework', 0)}/{ledger_stats.get('close_events', 0)}` ({round(ledger_stats.get('rework_declaration_rate', 0.0) * 100, 1)}%) | Close rows declaring a `rework=#N` or `rework=none` disposition — {format_rework_declaration_note(ledger_stats)} |",
        f"| **Rework Share** | `{round(rework_stats.get('rework_share', 0.0) * 100, 1)}%` | {rework_stats.get('total_entries', 0)} rework entries ÷ ({rework_stats.get('closed_subjects', 0)} closed subjects + {rework_stats.get('total_entries', 0)} rework entries) |",
        f"| **Rework per Close** | `{round(rework_stats.get('rework_per_close', 0.0) * 100, 1)}%` | {rework_stats.get('total_entries', 0)} rework entries ÷ {rework_stats.get('closed_subjects', 0)} closed subjects |",
        f"| **Change Fail Rate** | `{round(rework_stats.get('change_fail_rate', 0.0) * 100, 1)}%` | {rework_stats.get('change_fail_rate_numerator', 0)} closes that produced a rework entry ÷ {rework_stats.get('change_fail_rate_denominator', 0)} closed work units — **linkage coverage `{rework_stats.get('subject_coverage_numerator', 0)}/{rework_stats.get('subject_coverage_denominator', 0)}`** entries carry a determinate Subject ({round(rework_stats.get('subject_coverage', 0.0) * 100, 1)}%); read the rate only against this coverage |",
        f"| **Avg Task Lead Time** | `{ledger_stats.get('avg_lead_time_sec', 0.0)}s` | Mean intake→close duration over {len(ledger_stats.get('lead_times_sec', []))} sampled subjects |",
        f"| **Total Inference Cost** | `${ledger_stats.get('total_cost_usd', 0.0):.4f}` | Tracked cost across ledger task telemetry — summed from each row's canonical TRAILER only, by position and across every event (issue #90) |",
        f"| **Telemetry Outside the Trailer** | `{ledger_stats.get('out_of_trailer_count', 0)}` row(s) | {format_out_of_trailer_note(ledger_stats)} |",
        f"| **Avg Cost / Closed Task** | `{format_cost_usd(ledger_stats.get('avg_cost_per_closed_task_usd'))}` | ${ledger_stats.get('total_cost_usd', 0.0):.4f} total cost ÷ {ledger_stats.get('closed_subjects', 0)} distinct closed subjects |",
        f"| **Cost / Successful Task** | `{format_cost_usd(ledger_stats.get('cost_per_successful_task_usd'))}` | {format_cost_note(ledger_stats)} |",
        f"| **Total Tokens (In/Out)** | `{ledger_stats.get('total_tokens_in', 0)} / {ledger_stats.get('total_tokens_out', 0)}` | Cumulative prompt and completion tokens |",
        f"| **Cadence Status** | `{render_cadence_label(cadence_stats)}` | Last run: {cadence_stats.get('hours_since_last_run')}h ago · record read: {cadence_stats.get('record_read')} · latest artifact: {cadence_stats.get('artifact_latest_date')} |",
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
    # THE RUN'S OWN CLOCK (#276). Read before anything else so `duration=` describes the
    # whole run this row records, never a literal and never a sub-section of it.
    started = time.monotonic()
    parser = argparse.ArgumentParser(description="Operational process audit runner.")
    parser.add_argument("--json", action="store_true", help="Output JSON results to stdout")
    parser.add_argument("--report", action="store_true", help="Generate dated markdown report in evidence/scores/")
    parser.add_argument("--output", type=str, default="", help="Custom report output file path")
    parser.add_argument("--stamp", action="store_true", help="Record run row into evidence/ledger.jsonl")
    parser.add_argument("--actor", type=str, default="hq", help="Actor role for ledger stamp (default: hq)")
    parser.add_argument(
        "--no-gates",
        action="store_true",
        help="Emit metrics only, skipping the mechanical gate suite (used by tests/test_audit_rates.py and tests/test_audit_tree_condition.py to avoid gate recursion). The verdict fields report null, never a green.",
    )
    args = parser.parse_args()

    ledger_file = REPO_ROOT / "evidence/ledger.jsonl"
    rework_file = REPO_ROOT / "evidence/rework.md"

    # START-OF-RUN SNAPSHOT INSTANT (#142, ruling n=919). Read HERE, immediately before the
    # ledger is parsed, so the report states the instant its figures describe rather than
    # implying they hold when the reader opens the file: this run's own row lands after this
    # line. Read, never composed -- it is a clock read at the point of the read.
    ledger_read_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ledger_stats, closed_subject_set = parse_ledger(ledger_file)
    rework_stats = parse_rework(rework_file, closed_subject_set)
    # This run's OWN date, read BEFORE cadence so the artifact this run writes (or wrote in an
    # earlier `--report` pass) is never counted as a hole against itself. Hoisted here from
    # below the gate loop, where it sat after the cadence call it is now an input to.
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    cadence_stats = check_cadence_integrity(
        ledger_file, REPO_ROOT / "evidence/scores", own_run_date=today
    )

    # WALL-CLOCK AROUND THE GATE LOOP (#71). The stamp row's `duration=` was the string
    # literal `4s`, so every daily self-audit row carried a duration the audit never
    # measured -- plausible, constant and wrong, which a reader comparing runs reads as a
    # stable cost while the run itself takes minutes. The population is the SAME one the
    # gate table below reports (the loop that runs the gates), measured the same way
    # `run_gate` measures each gate, so the row and the table describe one run.
    # THE TREE THIS RUN READ (#150), computed ONCE and printed FROM this value by both
    # surfaces -- a reader that recomputes the condition can disagree with the run that
    # experienced it (HQ ruling n=1167). It describes the WORKING TREE, which is the only
    # tree a run has, so a peer lane's half-written file is named rather than silently
    # attributed to HEAD.
    tree = tree_condition(REPO_ROOT)

    t_gates0 = datetime.datetime.now()
    if args.no_gates:
        # SKIPPED, never silently GREEN: `judge_gates(gates_ran=False)` returns a null
        # verdict, so `--no-gates` cannot be mistaken for a passing suite. The flag exists
        # because a gate that runs `audit.py` would otherwise recurse into itself.
        gate_results = []
    else:
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
    gates_ran = not args.no_gates
    verdict = judge_gates(gate_results, cadence_ok=cadence_ok, gates_ran=gates_ran)
    # KEEPS THE bool/None SHAPE (#28): `bool(None)` is False, which turned a SKIPPED
    # suite into a measured failure -- the same vacuous-verdict defect in the opposite
    # direction. None means "no verdict was taken", and the three consumers below each
    # say so in their own vocabulary rather than folding it into a red.
    healthy = verdict.healthy

    if args.json:
        payload = {
            "date": today,
            "tree": tree,
            "gates_skipped": not gates_ran,
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
        # `healthy is None` is the SKIPPED suite (--no-gates), not a failure: nothing was
        # measured, so nothing failed. Folding it into the 1 a gate failure returns would
        # make "did not run" indistinguishable from "ran and failed".
        return 0 if (healthy is None or healthy) else 1

    # Stamp BEFORE reporting (#38) — a scorecard is a statement about the ledger it is
    # written into, so the run row must exist before the figures are taken. Reporting
    # first made every scorecard one row stale BY CONSTRUCTION: its "Total Ledger
    # Events" and its First-Pass Yield described the ledger as it stood BEFORE the run
    # being reported, and the error ran in the favourable direction — the run that just
    # failed was not yet in its own denominator. Order is stamp -> re-parse -> report.
    if args.stamp:
        outcome = "accepted" if (healthy is None or healthy) else "failed"
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
        # THE YIELD THIS ROW CARRIES IS THE POST-RUN FIGURE — the same population the
        # scorecard below reports — so the two artifacts of one run state one number (#38,
        # #143). It is derived rather than read back because the row must be written before
        # the ledger can be re-read; `healthy` is this run's own outcome, so its contribution
        # to accepted/population is already known here. `ledger_stats` is REBOUND to that
        # post-run view for exactly one purpose: the row renders through the ONE rounding
        # site, `format_run_row_detail`, and passing it the pre-stamp dict would publish a
        # second number for the same run.
        pre_runs = ledger_stats.get("run_events", 0)
        pre_population = ledger_stats.get("first_pass_yield_population", 0)
        pre_accepted = ledger_stats.get("runs_by_outcome", {}).get("accepted", 0)
        post_runs = pre_runs + 1
        post_population = pre_population + 1
        post_accepted = pre_accepted + (1 if outcome == "accepted" else 0)
        ledger_stats = {
            **ledger_stats,
            "run_events": post_runs,
            "run_rows_stating_outcome": post_population,
            "first_pass_yield_population": post_population,
            "first_pass_yield_coverage": [post_population, post_runs],
            "first_pass_yield": (
                post_accepted / post_population if post_population else None
            ),
            "runs_by_outcome": {
                **ledger_stats.get("runs_by_outcome", {}),
                "accepted": post_accepted,
            },
        }
        # MEASURED, never a literal (#71, #276): `elapsed_secs` is this run's own wall clock,
        # read from `started` at the top of `main()`. `turns=0` stays a legitimate
        # declaration — the runner makes no agent turns. The three-state gate token is
        # carried by `gate=`, which names `gate-unknown` explicitly (#72).
        #
        # ROUNDED UP, and the direction is load-bearing. `format_elapsed_seconds` TRUNCATES
        # to integer seconds — the kit's contract, asserted at
        # tests/test_audit_rates.py:1327-1330 (`0.4 -> "0s"`, `59.9 -> "59s"`), so it is not
        # ours to change. Handed a raw wall, a run of 1.29s stamps `duration=1s`, which sits
        # BELOW the gate table that same run printed — the row under-reporting the work it
        # describes, the exact defect tests/test_audit_stamp_duration.py exists to catch, and
        # it fired on the first full audit after this port (`sleep=1.0s tree: the row says
        # duration=1.0s while the gate table it printed in the SAME run sums to 1.29s`).
        # Rounding DOWN was the pre-port form only because that site rendered 2 dp; the kit's
        # integer renderer cannot carry the fraction, so the only direction that keeps the
        # row at or above its own gate table is UP. The cost is at most one second of
        # over-report on a run measured in minutes, and a duration that never under-states
        # the work is the property the guard asserts.
        elapsed_secs = math.ceil(time.monotonic() - started)
        detail = format_run_row_detail(ledger_stats, outcome, gate_summary, elapsed_secs)
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
        ledger_stats, _ = parse_ledger(ledger_file)
    # Text summary output
    print(f"=== Factory Operational Self-Audit ({today}) ===")
    # The THREE-state headline (#72). `render_status_line` is the one place the state is
    # spelled, so the stdout line, the JSON `status` and the stamp row's `verdict=` token
    # cannot describe different runs. A timeout reads UNKNOWN here, never DEGRADED: the box
    # was loaded, the gate was healthy, and the audit took no verdict on it.
    print(render_status_line(verdict))
    # The rate is WITHHELD below RATE_FLOOR (#77); the counts print regardless, so a small
    # denominator is visible in the output rather than inferred from a rate that reads as a verdict.
    # EVERY rate here travels beside its population: `format_yield_text` carries the
    # coverage, `format_cost_per_success_text` names the successful-close population, and
    # the rework figures print numerator and denominator in the same line. A bare rate is
    # never read alone (ruling n=386 clause 5); the figures are PRINTED, never gated
    # (clause 6 / n=328).
    print(f"  - First-Pass Yield: {format_yield_text(ledger_stats)}")
    print(f"  - Cost / Successful Task: {format_cost_per_success_text(ledger_stats)}")
    print(f"  - Telemetry Outside the Trailer: {ledger_stats.get('out_of_trailer_count', 0)} row(s) — {format_out_of_trailer_note(ledger_stats)}")
    print(f"  - Undeclared Outcomes: {format_undeclared_text(ledger_stats)}")
    print(f"  - Closed Subjects: {ledger_stats.get('closed_tasks', 0)} (close rows: {ledger_stats.get('close_events', 0)}) | Intake Subjects: {ledger_stats.get('intake_tasks', 0)}")
    print(f"  - Rework Entries: {rework_stats.get('total_entries', 0)} (share: {round(rework_stats.get('rework_share', 0.0) * 100, 1)}% = {rework_stats.get('total_entries', 0)} ÷ ({rework_stats.get('closed_subjects', 0)} + {rework_stats.get('total_entries', 0)}) | per close: {round(rework_stats.get('rework_per_close', 0.0) * 100, 1)}% = {rework_stats.get('total_entries', 0)} ÷ {rework_stats.get('closed_subjects', 0)})")
    print(f"  - Rework Declarations: {ledger_stats.get('close_rows_declaring_rework', 0)}/{ledger_stats.get('close_events', 0)} close rows declare a disposition ({round(ledger_stats.get('rework_declaration_rate', 0.0) * 100, 1)}%) — {format_rework_declaration_note(ledger_stats)}")
    print(f"  - Change Fail Rate: {round(rework_stats.get('change_fail_rate', 0.0) * 100, 1)}% = {rework_stats.get('change_fail_rate_numerator', 0)} ÷ {rework_stats.get('change_fail_rate_denominator', 0)} closed work units (linkage coverage: {rework_stats.get('subject_coverage_numerator', 0)}/{rework_stats.get('subject_coverage_denominator', 0)} entries carry a determinate Subject — a bare rate is never read alone)")
    print(f"  - Cadence: {render_cadence_label(cadence_stats)} (last run: {cadence_stats.get('hours_since_last_run')}h ago · record read: {cadence_stats.get('record_read')} · latest artifact: {cadence_stats.get('artifact_latest_date')})")
    unknown_note = f", {len(verdict.unknown)} UNKNOWN" if verdict.unknown else ""
    print(f"\nMechanical Gates ({len(gate_results)}{unknown_note}):")
    for g in gate_results:
        # `render_gate_line` carries the mark, the elapsed time, the declared budget and the
        # gate's own cause on ONE line, so a reader triaging the table does not have to open
        # the report to learn WHY a gate did not pass (#74 leg 7).
        print(render_gate_line(g))

    if args.report or args.output:
        report_md = format_report_markdown(today, ledger_read_at, ledger_stats, rework_stats, cadence_stats, gate_results)
        out_path = Path(args.output) if args.output else (REPO_ROOT / f"evidence/scores/{today}-self-audit.md")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report_md, encoding="utf-8")
        print(f"\nAudit report written to: {out_path}")

    return 0 if (healthy is None or healthy) else 1


if __name__ == "__main__":
    sys.exit(main())
