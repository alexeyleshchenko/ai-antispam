#!/usr/bin/env python3
"""Gate: every scorecard artifact date carries a self-audit run row in the ledger.

WHY THIS FILE EXISTS (#159). The artifact series (`evidence/scores/<date>-self-audit.md`)
and the ledger are two records of the same daily run: the artifact is the run ITSELF, the
ledger row is its HANDOFF. The cadence leg compares them, but only through a 7-day lookback
window and only for the HOLE set -- nothing asserted the whole series. So a day whose handoff
row never landed aged out of the window and left no trace, and the artifact series could grow
one-sidedly: 61 artifacts against 57 distinct run dates, measured 2026-11-21.

The check is one line of arithmetic -- every artifact date must appear among the `ts` dates of
`run`/`score` rows the subject predicate accepts -- and it is done HERE rather than in the
cadence leg because the cadence leg is bounded by design (an old hole has no repair space and
an unbounded rule would be a permanent red). This gate is not unbounded either: an artifact
date with no row is EXCUSED only through a dated admission in
`docs/cadence-crosscheck-exemptions.json`, printed on every run as visible debt, and never
backfilled -- the ledger is append-only and `append` has no `--ts`, so a retro row cannot
carry the day's true stamp.

THE OWN-RUN-DATE EXCLUSION, and why it is not a hole. The process computing this crosscheck
owns today's artifact: the report is rendered and committed before the handoff row is stamped
(a cron reports in one invocation and the row lands in another). Judging today's date would
red a clean run on its own half-written state -- the 2026-11-03 false red (#124's measured
lesson, `own_run_date` in `check_cadence_integrity`). Today is printed in the control line and
is caught by the NEXT run, because this gate reads the whole series rather than a window.

Run:  python3 tests/test_audit_cadence_artifact_crosscheck.py [--scores DIR] [--ledger PATH]
      [--own-run-date YYYY-MM-DD]
Exit: 0 clean (or SKIPPED with its stated reason when the tree carries no artifact series),
      1 on any artifact date with no run row and no exemption, a STALE or PHANTOM or
      malformed exemption, an unreadable ledger line, or a zero-artifact series.
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import audit  # noqa: E402

SCORES_DEFAULT = REPO / "evidence" / "scores"
LEDGER_DEFAULT = REPO / "evidence" / "ledger.jsonl"
EXEMPTIONS_PATH = REPO / "docs" / "cadence-crosscheck-exemptions.json"

# The artifact naming law, read from the directory rather than assumed: every file under
# `evidence/scores/` is `<YYYY-MM-DD>-self-audit.md`. A file that does not match is NAMED as a
# problem -- skipping it silently would let an artifact leave the population unexamined, which
# is the exempt-by-silence class this repo's gate law refuses.
ARTIFACT_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-self-audit\.md$")

def _today() -> str:
    """The run's own date, on the same clock `tools/audit.py` stamps with."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

def artifact_dates(scores_dir: Path, problems: list[str]) -> list[str]:
    """The DATE each artifact filename carries, ascending; a non-conforming name is a problem."""
    dates: list[str] = []
    for path in sorted(scores_dir.glob("*.md")):
        match = ARTIFACT_RE.match(path.name)
        if not match:
            problems.append(
                f"{path.name}: not a scorecard artifact name (`<YYYY-MM-DD>-self-audit.md`), "
                f"so its date cannot be crosschecked against the ledger"
            )
            continue
        dates.append(match.group(1))
    return dates

def ledger_run_dates(ledger_path: Path, problems: list[str]) -> set[str]:
    """The `ts` dates of `run`/`score` rows the self-audit subject predicate ACCEPTS.

    The acceptance rule is `audit.is_self_audit_subject` -- the ONE predicate, never a second
    spelling test here: this gate must agree with the cadence leg about what a self-audit row
    is, or the two would disagree about the same ledger. A corrupt line is REPORTED rather
    than skipped: a declared record that cannot be read is a defect, not an absence.
    """
    dates: set[str] = set()
    with open(ledger_path, "r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception as exc:
                problems.append(f"{ledger_path}:{number}: unreadable ledger line ({exc})")
                continue
            if row.get("event") not in ("run", "score"):
                continue
            if not audit.is_self_audit_subject(row.get("subject")):
                continue
            ts = row.get("ts")
            if isinstance(ts, str) and len(ts) >= 10:
                dates.add(ts[:10])
    return dates

def load_exemptions(path: Path, problems: list[str]) -> dict[str, str]:
    """`date -> reason` from the factory's exemption table; malformed entries FAIL by name.

    The live table is read; its `.example.json` skeleton is the fallback, so a bootstrapped
    factory that has not yet admitted anything reads as "no exemptions" rather than as "no
    surface" (#112's shape). Every entry must carry a non-blank `reason` and a non-blank
    `empty_repair_space`: an exemption that does not say why the repair space is empty is an
    exempt-by-silence, the class n=571 names.
    """
    if not path.is_file():
        skeleton = path.with_name("cadence-crosscheck-exemptions.example.json")
        if not skeleton.is_file():
            return {}
        path = skeleton
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        problems.append(f"{path}: unreadable exemption table ({exc})")
        return {}
    entries = data.get("exemptions")
    if not isinstance(entries, list):
        problems.append(f"{path}: `exemptions` is not a list; the table cannot be read")
        return {}
    out: dict[str, str] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append(f"{path}: exemption entry is not an object: {entry!r}")
            continue
        date = entry.get("date")
        if not isinstance(date, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            problems.append(f"{path}: exemption carries no `YYYY-MM-DD` date: {entry!r}")
            continue
        reason = str(entry.get("reason") or "").strip()
        repair = str(entry.get("empty_repair_space") or "").strip()
        if not reason:
            problems.append(f"{path}: exemption for {date} carries NO REASON (exempt-by-silence)")
            continue
        if not repair:
            problems.append(f"{path}: exemption for {date} states no EMPTY REPAIR SPACE")
            continue
        out[date] = reason
    return out

def _parse_args(argv: list[str]) -> tuple[Path, Path, Path, str, list[str]]:
    """`--scores`, `--ledger`, `--exemptions`, `--own-run-date`; anything else is a usage error.

    The overrides exist so a FIXTURE can drive the gate (the bite proof does exactly that):
    pointing at a throwaway series must not make the gate read this factory's live exemption
    table, or a fixture run would report the live entries as PHANTOM.
    """
    scores, ledger, exemptions, own = (
        SCORES_DEFAULT,
        LEDGER_DEFAULT,
        EXEMPTIONS_PATH,
        _today(),
    )
    problems: list[str] = []
    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag in ("--scores", "--ledger", "--exemptions", "--own-run-date") and args:
            value = args.pop(0)
            if flag == "--scores":
                scores = Path(value)
            elif flag == "--ledger":
                ledger = Path(value)
            elif flag == "--exemptions":
                exemptions = Path(value)
            else:
                own = value
        else:
            problems.append(f"unrecognised argument {flag!r}")
    return scores, ledger, exemptions, own, problems

def main(argv: list[str] | None = None) -> int:
    scores_dir, ledger_path, exemptions_path, own_run_date, problems = _parse_args(
        list(sys.argv[1:] if argv is None else argv)
    )

    print("Gate: every scorecard artifact date carries a self-audit run row (#159)")
    print("  record: evidence/ledger.jsonl run/score rows whose subject matches is_self_audit_subject")
    print(f"  exempt: {exemptions_path} (dated entries, printed on every run)")

    if not scores_dir.is_dir():
        # The ledger boundary (`tests/ledger_boundary.py`): a tree with no artifact series has
        # nothing to judge, and says so. Never a silent pass, and never another tree's data.
        print(f"SKIP: no artifact series at {scores_dir} -- this tree carries no scorecards to crosscheck")
        return 0 if not problems else _report(problems)

    dates = artifact_dates(scores_dir, problems)
    ledger_read = True
    if not ledger_path.is_file():
        problems.append(
            f"{ledger_path}: the ledger is missing while {len(dates)} artifact date(s) exist; "
            f"the series cannot be crosschecked"
        )
        ledger_read = False
        rows: set[str] = set()
    else:
        rows = ledger_run_dates(ledger_path, problems)

    exemptions = load_exemptions(exemptions_path, problems)

    uncovered = [date for date in dates if date not in rows]
    excused: list[str] = []
    for date in uncovered:
        if date == own_run_date:
            continue  # own artifact; its row lands after this gate. Caught by the next run.
        if date in exemptions:
            excused.append(date)
            continue
        if not ledger_read:
            continue  # the missing ledger is already named; one problem, not one per date
        problems.append(
            f"{date}: artifact `evidence/scores/{date}-self-audit.md` exists and the ledger "
            f"carries NO self-audit run/score row dated {date} -- the handoff was dropped"
        )

    for date in excused:
        print(f"  excused: {date} -- {exemptions[date][:110]}")

    # An exemption must be live debt, never a stale or phantom entry: a stale one forgives a
    # violation that no longer exists, a phantom one names a day the series does not carry.
    for date in sorted(exemptions):
        if date in rows:
            problems.append(
                f"{date}: exemption is STALE -- the ledger DOES carry a self-audit row for {date}"
            )
        elif date not in dates:
            problems.append(
                f"{date}: exemption is PHANTOM -- no artifact under {scores_dir} is dated {date}"
            )

    covered = sum(1 for date in dates if date in rows)
    own_note = ""
    if own_run_date in dates and own_run_date not in rows:
        own_note = f" · own run date {own_run_date} excluded (its row lands after this gate)"
    print(
        f"  examined {len(dates)} artifact date(s), {covered} with a self-audit run row, "
        f"{len(uncovered)} without ({len(excused)} excused){own_note}"
    )

    if not dates:
        # A zero-artifact run must never read as clean: an empty population is a broken
        # instrument, not a healthy series (rule 7 -- a zero is a verdict only from a
        # working instrument).
        problems.append(
            f"{scores_dir}: ZERO artifact dates examined; an empty series cannot report a clean "
            f"crosscheck"
        )
    return _report(problems)

def _report(problems: list[str]) -> int:
    if problems:
        print(f"FAIL: {len(problems)} problem(s)")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("PASS: every artifact date carries a self-audit run row, or a dated exemption")
    return 0

if __name__ == "__main__":
    sys.exit(main())
