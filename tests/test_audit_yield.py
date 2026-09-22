"""#38 guard: the run row and the scorecard state the SAME yield for ONE run.

Why this file exists
--------------------
`tools/audit.py` produced two different yields for a single run. The parser
pre-rounded the ratio to 4 dp, and the two display sites then rounded that
already-rounded value differently:

    row     int(v * 100)          ->  int(0.8235 * 100) = 82    ->  "82%"
    report  round(v * 100, 1)     ->  82.3                      ->  "82.3%"

A *comment* documenting that cannot fail, so pre-rounding could be reintroduced
with nothing going red. These checks are the guard the comment claimed.

The invariant is now structural rather than arithmetic: percentages come from
ONE site, `audit.first_pass_yield_pct(accepted, total)`. The end-to-end leg
below runs the real tool in a throwaway tree and compares the two delivered
artifacts, so it fails on ANY reintroduced second expression -- not merely the
two that were there before.

Runs two ways, matching the sibling gates: as a script (`python3
tests/test_audit_yield.py`, which is how tools/audit.py invokes it and how the
daily pacemaker therefore reaches it) and under pytest, which collects the
`test_*` wrappers.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

from audit import first_pass_yield_pct  # noqa: E402


def check_single_site() -> list[str]:
    """The percentage is produced in exactly one place, and it is correct there."""
    problems: list[str] = []

    if first_pass_yield_pct(14, 17) != 82.4:
        problems.append(
            f"first_pass_yield_pct(14, 17) = {first_pass_yield_pct(14, 17)}, expected 82.4"
        )
    if first_pass_yield_pct(0, 0) != 0.0:
        problems.append("first_pass_yield_pct(0, 0) must be 0.0, not a ZeroDivisionError")

    # The guard must be able to FAIL: this is the expression the fix replaced,
    # and it reads a different number at the first reachable divergent ratio.
    old_row_pct = int(round(14 / 17, 4) * 100)
    if old_row_pct == first_pass_yield_pct(14, 17):
        problems.append(
            "the pre-fix expression agrees with the fixed one at 14/17 -- this "
            "check no longer discriminates and cannot detect a regression"
        )

    return problems


def _append_run(tree: Path, outcome: str) -> None:
    res = subprocess.run(
        [
            sys.executable,
            "tools/ledger.py",
            "append",
            "--event",
            "run",
            "--actor",
            "hq",
            "--subject",
            "internal-self-audit",
            "--detail",
            f"duration=4s turns=0 outcome={outcome} gate=all-pass yield=0%",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"seed append failed:\n{res.stdout}{res.stderr}")


def check_end_to_end(base: Path) -> list[str]:
    """Run the real tool in a throwaway tree and compare its two artifacts.

    This is the #38 close condition itself, asserted: the scorecard's "Total
    Ledger Events" must equal the live ledger row count at the instant the
    report is written, and its First-Pass Yield must equal the yield the run
    row carries -- same run, same population, one number.

    The fixture is seeded through `tools/ledger.py append` rather than written
    by hand, so it is valid by construction and cannot drift from the ledger's
    own schema rules.
    """
    problems: list[str] = []
    tree = base / "repo"
    (tree / "tools").mkdir(parents=True)
    (tree / "evidence").mkdir()
    shutil.copy(REPO / "tools" / "audit.py", tree / "tools" / "audit.py")
    shutil.copy(REPO / "tools" / "ledger.py", tree / "tools" / "ledger.py")

    # 13 accepted + 3 failed, then this run is accepted -> 14/17, the first ratio
    # where the two old expressions disagreed.
    for _ in range(13):
        _append_run(tree, "accepted")
    for _ in range(3):
        _append_run(tree, "failed")

    scorecard = tree / "evidence" / "scores" / "today.md"
    res = subprocess.run(
        [
            sys.executable,
            "tools/audit.py",
            "--report",
            "--stamp",
            "--output",
            str(scorecard),
        ],
        cwd=tree,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        return [f"audit run failed (rc={res.returncode}):\n{res.stdout}{res.stderr}"]

    rows = [
        json.loads(line)
        for line in (tree / "evidence" / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows or rows[-1].get("event") != "run":
        return [f"the run row was not written last: {rows[-1] if rows else 'no rows'}"]

    row_yield = re.search(r"yield=([\d.]+)%", rows[-1].get("detail", ""))
    if not row_yield:
        return [f"no yield= field on the run row: {rows[-1].get('detail')!r}"]

    body = scorecard.read_text(encoding="utf-8")
    total = re.search(r"\*\*Total Ledger Events\*\* \| `(\d+)`", body)
    report_yield = re.search(r"\*\*First-Pass Yield\*\* \| `([\d.]+)%`", body)
    if not total:
        return ["scorecard carries no Total Ledger Events row"]
    if not report_yield:
        return ["scorecard carries no First-Pass Yield row"]

    if int(total.group(1)) != len(rows):
        problems.append(
            f"scorecard says {total.group(1)} ledger events, the ledger holds "
            f"{len(rows)} -- the report does not describe the ledger it was written into"
        )
    if row_yield.group(1) != report_yield.group(1):
        problems.append(
            f"one run, two yields: run row {row_yield.group(1)}% vs scorecard "
            f"{report_yield.group(1)}%"
        )
    if report_yield.group(1) != "82.4":
        problems.append(
            f"expected 82.4% at 14/17 (the first divergent ratio), got {report_yield.group(1)}%"
        )

    return problems


# --- pytest wrappers (the gate path does not need pytest) -------------------

def test_single_rounding_site() -> None:
    assert check_single_site() == []


def test_row_and_scorecard_agree_end_to_end(tmp_path: Path) -> None:
    assert check_end_to_end(tmp_path) == []


# --- standalone entry point (how tools/audit.py runs it) --------------------

def main() -> int:
    problems = check_single_site()
    with tempfile.TemporaryDirectory(prefix="audit-yield-") as td:
        problems += check_end_to_end(Path(td))

    if problems:
        print(f"yield-artifact guard FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  {p}")
        return 1

    print("yield-artifact guard clean: one rounding site, row == scorecard at 14/17")
    return 0


if __name__ == "__main__":
    sys.exit(main())
