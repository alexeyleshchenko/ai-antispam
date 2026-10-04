"""#77 guard: a rate over a denominator below the floor is WITHHELD, never printed as a verdict.

Why this file exists
--------------------
`tools/audit.py` rendered **First-Pass Yield** and **Rework Rate** unconditionally, with
no floor on the rate path. Over a small denominator a rate is noise read as a verdict:
one close carrying one rework printed a **300%** rework rate, and nothing in the output
said the denominator was 1.

The fix withholds the RATE when its denominator sits below `RATE_FLOOR`, names the floor
inside the withheld string, and always prints the COUNTS. One site decides --
`audit.render_rate_pct` -- for the same single-site reason `first_pass_yield_pct` exists
for the rounding (#38): a second copy of the predicate is a second thing to keep in step.

Four acceptance bullets (#77), each asserted against a real artifact rather than a comment:
  1. below the floor  -> the rate is withheld, not a number
  2. at/above the floor -> the rate renders
  3. the floor value is printed beside the rate
  4. the counts print in BOTH cases

Runs two ways, matching the sibling guards: as a script (`python3 tests/test_rate_floor.py`)
and under pytest, which collects the `test_*` wrappers.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit import RATE_FLOOR, render_rate_pct  # noqa: E402
from gate_fixtures import stage_tool  # noqa: E402

def check_helper() -> list[str]:
    """Bullets 1-3 at the ONE site that decides: withheld below, rendered at/above, floor named."""
    problems: list[str] = []

    below = render_rate_pct(300.0, 1)
    if "withheld" not in below:
        problems.append(f"n=1 is below floor {RATE_FLOOR} and must be withheld, got {below!r}")
    if "300.0%" in below:
        problems.append(f"the withheld string still carries the rate it withheld: {below!r}")
    if f"floor {RATE_FLOOR}" not in below:
        problems.append(f"the withheld string does not name the floor: {below!r}")
    if "n=1" not in below:
        problems.append(f"the withheld string does not state its denominator: {below!r}")

    # AT the floor and ABOVE it the rate renders. Both are asserted: a guard that withheld
    # everything would satisfy a below-floor-only check and print no rate at all.
    at = render_rate_pct(300.0, RATE_FLOOR)
    if at != "300.0%":
        problems.append(f"n == floor {RATE_FLOOR} must render the rate, got {at!r}")
    above = render_rate_pct(82.4, 17)
    if above != "82.4%":
        problems.append(f"n=17 is above floor {RATE_FLOOR} and must render, got {above!r}")

    # The rework leg's zero-denominator shape: no closed tasks means no rate to read, and
    # `0.0%` is exactly the verdict-shaped noise this guard removes.
    if "withheld" not in render_rate_pct(0.0, 0):
        problems.append("n=0 (no closed tasks) must be withheld, not printed as 0.0%")

    return problems

def _seed_run(tree: Path, outcome: str) -> None:
    res = subprocess.run(
        [
            sys.executable, "tools/ledger.py", "append",
            "--event", "run", "--actor", "hq", "--subject", "internal-self-audit",
            "--detail", f"duration=4s turns=0 outcome={outcome} gate=all-pass yield=0%",
        ],
        cwd=tree,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            # The migrated tool resolves an --actor through the fleet registry unless the
            # append is marked a FIXTURE, and the mark is `OC_LEDGER_PATH` being set
            # (tools/ledger.py:643). Pinned to the SAME file the tree uses, so the seam is
            # honoured without moving the ledger out from under tools/audit.py, which
            # resolves evidence/ledger.jsonl from its own REPO_ROOT and honours no override.
            "OC_LEDGER_PATH": str(tree / "evidence" / "ledger.jsonl"),
            "OC_ACTORS_PATH": str(tree / "evidence" / "actors-fixture.txt"),
        },
    )
    if res.returncode != 0:
        raise RuntimeError(f"seed append failed:\n{res.stdout}{res.stderr}")

def _run_audit(base: Path, runs: int) -> tuple[str, str]:
    """Run the REAL tool in a throwaway tree and return (scorecard markdown, stdout)."""
    tree = base / "repo"
    (tree / "tools").mkdir(parents=True)
    (tree / "evidence").mkdir()
    # The closure travels with the tool (P35, meta-factory #60): stage_tool computes it, so
    # nothing here hardcodes which modules ledger.py imports.
    stage_tool(REPO / "tools" / "audit.py", tree / "tools", REPO / "tools")
    stage_tool(REPO / "tools" / "ledger.py", tree / "tools", REPO / "tools")
    for _ in range(runs):
        _seed_run(tree, "accepted")

    scorecard = tree / "evidence" / "scores" / "today.md"
    res = subprocess.run(
        [sys.executable, "tools/audit.py", "--report", "--output", str(scorecard)],
        cwd=tree,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "OC_LEDGER_PATH": str(tree / "evidence" / "ledger.jsonl"),
            "OC_ACTORS_PATH": str(tree / "evidence" / "actors-fixture.txt"),
        },
    )
    if res.returncode != 0:
        raise RuntimeError(f"audit run failed (rc={res.returncode}):\n{res.stdout}{res.stderr}")
    return scorecard.read_text(encoding="utf-8"), res.stdout

def _cell(body: str, label: str) -> str | None:
    m = re.search(rf"\*\*{re.escape(label)}\*\* \| `([^`]+)`", body)
    return m.group(1) if m else None

def check_render_sites(base: Path) -> list[str]:
    """All four bullets at the DELIVERED artifacts: the scorecard markdown and the stdout line."""
    problems: list[str] = []

    # --- below the floor: one accepted run (yield denominator 1), no closed tasks -------
    body, out = _run_audit(base / "below", 1)

    y = _cell(body, "First-Pass Yield")
    r = _cell(body, "Rework Rate")
    if y is None:
        problems.append("scorecard carries no First-Pass Yield row")
    elif "withheld" not in y:
        problems.append(f"1 run is below floor {RATE_FLOOR}, yet a yield printed: {y!r}")
    if r is None:
        problems.append("scorecard carries no Rework Rate row")
    elif "withheld" not in r:
        problems.append(f"0 closed tasks is below floor {RATE_FLOOR}, yet a rate printed: {r!r}")

    # Bullet 3 -- the floor is named in the delivered output, not merely in the source.
    if y is not None and f"floor {RATE_FLOOR}" not in y:
        problems.append(f"the withheld yield does not name the floor: {y!r}")
    if f"floor {RATE_FLOOR}" not in body:
        problems.append("the scorecard's derivation cells never state the floor")

    # Bullet 4 -- the counts print in BOTH cases (below the floor, here).
    if _cell(body, "Rework Entries") is None:
        problems.append("below the floor, the rework ENTRY COUNT must still print")
    if _cell(body, "Closed Tasks") is None:
        problems.append("below the floor, the Closed Tasks count must still print")
    if "First-Pass Yield: withheld" not in out:
        problems.append(f"the stdout line did not withhold the yield:\n{out}")
    if "(1/1 accepted)" not in out:
        problems.append(f"the stdout line must still print the counts:\n{out}")

    # --- at the floor: five accepted runs -> the yield denominator reaches RATE_FLOOR ----
    # `max(RATE_FLOOR, 1)` keeps the tree non-degenerate if the constant is ever edited down:
    # an EMPTY ledger reads DEGRADED (cadence MISSED) and rc=1, which would surface as a
    # fixture crash instead of a reported problem about the floor itself.
    body_at, out_at = _run_audit(base / "at", max(RATE_FLOOR, 1))
    y_at = _cell(body_at, "First-Pass Yield")
    if y_at is None:
        problems.append("at the floor, the scorecard carries no First-Pass Yield row")
    elif not re.fullmatch(r"[\d.]+%", y_at):
        problems.append(f"n == floor {RATE_FLOOR} must RENDER the yield, got {y_at!r}")
    elif y_at != "100.0%":
        problems.append(f"5/5 accepted runs must read 100.0%, got {y_at!r}")

    # The two legs discriminate in ONE scorecard: the yield renders while the rework rate
    # is withheld, so a blanket withhold (or a blanket render) cannot pass both.
    r_at = _cell(body_at, "Rework Rate")
    if r_at is None or "withheld" not in r_at:
        problems.append(
            f"with 0 closed tasks the rework rate must still withhold while the yield "
            f"renders, got {r_at!r}"
        )
    if "First-Pass Yield: 100.0%" not in out_at:
        problems.append(f"at the floor the stdout line must render the yield:\n{out_at}")

    return problems

# --- pytest wrappers (the script path below does not need pytest) -------------------

def test_rate_floor_helper() -> None:
    assert check_helper() == []

def test_rate_floor_render_sites(tmp_path: Path) -> None:
    assert check_render_sites(tmp_path) == []

# --- standalone entry point ---------------------------------------------------------

def main() -> int:
    problems = check_helper()
    with tempfile.TemporaryDirectory(prefix="rate-floor-") as td:
        problems += check_render_sites(Path(td))

    if problems:
        print(f"rate-floor guard FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print(f"  {p}")
        return 1

    print(
        f"rate-floor guard clean: rates withheld below floor {RATE_FLOOR}, rendered at and "
        f"above it, counts always printed"
    )
    return 0

if __name__ == "__main__":
    sys.exit(main())
