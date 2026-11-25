#!/usr/bin/env python3
"""Gate: a dependency failure is ONE environment leg, never N counted defects.

Why this file exists
--------------------
#158 clause 2. The 2026-11-20 scorecard reported 60 gates, 44 PASS, 16 FAIL -- 15 of them
`ModuleNotFoundError: No module named 'aiosqlite'` from one host alternatives flip, and one
`SyntaxError` from PEP 758 syntax that parses on 3.14 and has been in the file since
2026-09-29. A reader saw sixteen unrelated defects and nothing on the artifact named the
single cause.

The fix classifies the affected rows: a gate that dies with `ModuleNotFoundError` for a
distribution DECLARED in `pyproject.toml` is folded into ONE environment leg naming the
interpreter that ran the gates and the missing distribution(s). The leg is an ADDITIONAL
fact -- every one of those rows is still a FAILURE and the verdict still reflects it, because
a run whose gates could not execute is not green.

Arms
----
A  DECLARED SET -- the reader sees `aiosqlite` (declared under `[dependency-groups] dev`,
   pyproject.toml:70) and `asyncpg` (`[project] dependencies`), and normalizes names.
B  ONE LEG      -- two real synthetic gates that genuinely die on a declared-but-absent
   distribution collapse to ONE leg naming the interpreter and that distribution; an
   undeclared import error and a passing gate are NOT folded in.
C  RENDERED     -- the scorecard carries the leg exactly ONCE, above the rows it explains,
   and those rows are still FAIL / DEGRADED.
D  NEUTER       -- the same render predicate against a REAL copy on disk with the leg's
   render call removed: it must go RED, so C proves the wiring, not a helper.

Runs two ways, matching the sibling gates: as a script
(`/usr/bin/python3.14 tests/test_audit_env_classification.py`, the form `__main__` owns) and
under pytest, which collects the `test_*` wrappers.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

REPO = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO / "tools" / "audit.py"

# A synthetic repo: the DECLARED distribution is one this box does not have, so the gates
# below fail with a GENUINE ModuleNotFoundError (a real traceback from a real subprocess),
# never a printed imitation of one.
SYNTH_PYPROJECT = '''[project]
name = "synthetic"
version = "0.0.0"
requires-python = ">=3.14"
dependencies = [
    "frobnicator>=1.0",
    "asyncpg>=0.30",
]

[project.optional-dependencies]
extras = [
    "PyYAML_Extra>=1.0",
]

[dependency-groups]
dev = [
    "aiosqlite>=0.22.1",
    {include-group = "lint"},
]
'''

GATE_IMPORTS_MISSING = '''import frobnicator  # noqa: F401  -- declared in the synthetic pyproject, absent here
print("unreachable")
'''
GATE_IMPORTS_UNDECLARED = '''import definitely_not_declared_anywhere  # noqa: F401
print("unreachable")
'''
GATE_PASSES = '''print("synthetic gate clean")
'''

def _load_audit(path: Path = AUDIT_PATH, name: str = "audit_env_under_test") -> ModuleType:
    """Load an audit module from `path` (registered before exec: `@dataclass` needs it)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return mod

def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path

def _synthetic_run(mod: ModuleType) -> tuple[list[dict[str, Any]], Path]:
    """Build a synthetic repo, run its gates for REAL, return (results, pyproject path)."""
    tmp = Path(tempfile.mkdtemp(prefix="env-class-"))
    pyproject = _write(tmp / "pyproject.toml", SYNTH_PYPROJECT)
    gates = [
        _write(tmp / "gates" / "declared_a.py", GATE_IMPORTS_MISSING),
        _write(tmp / "gates" / "declared_b.py", GATE_IMPORTS_MISSING),
        _write(tmp / "gates" / "undeclared.py", GATE_IMPORTS_UNDECLARED),
        _write(tmp / "gates" / "passing.py", GATE_PASSES),
    ]
    results = [mod.run_gate([sys.executable, str(g)], tmp) for g in gates]
    return results, pyproject

# --- Arm A: the declared set -------------------------------------------------------

def test_arm_a_real_pyproject_declares_the_incident_distributions() -> None:
    """`aiosqlite` lives in [dependency-groups] dev and MUST be seen -- that is the incident."""
    mod = _load_audit()
    declared = mod.read_declared_distributions()
    for name in ("aiosqlite", "asyncpg"):
        assert declared.get(name) == name, (
            f"{name!r} must be read from pyproject.toml and map to itself; got "
            f"{declared.get(name)!r}"
        )

def test_arm_a2_names_normalize_and_every_group_is_read() -> None:
    """PEP 503 normalization, and the optional + dependency-group tables are read too."""
    mod = _load_audit()
    norm = mod.normalize_distribution_name
    assert norm("PyYAML") == norm("pyyaml") == "pyyaml"
    assert norm("pydantic_ai_slim") == norm("pydantic-ai-slim")
    with tempfile.TemporaryDirectory() as tmp:
        pyproject = _write(Path(tmp) / "pyproject.toml", SYNTH_PYPROJECT)
        declared = mod.read_declared_distributions(pyproject)
    assert declared.get("frobnicator") == "frobnicator", declared  # extras stripped
    assert declared.get("pyyaml-extra") == "PyYAML_Extra", declared  # optional table
    assert declared.get("aiosqlite") == "aiosqlite", declared  # dependency-groups
    assert len(declared) == 4, declared  # the {include-group} entry declares no distribution

# --- Arm B: ONE leg ----------------------------------------------------------------

def test_arm_b_declared_failures_collapse_to_one_leg() -> None:
    """Two gates dying on ONE declared distribution produce ONE leg naming it and the interpreter."""
    mod = _load_audit()
    results, pyproject = _synthetic_run(mod)
    assert sum(1 for g in results if not g["passed"]) == 3, [g["cmd"] for g in results]
    leg = mod.classify_environment_failures(
        results, interpreter="/usr/bin/python3.14", pyproject_path=pyproject
    )
    assert leg is not None, "the declared failure was not classified at all"
    assert leg["gate_count"] == 2, leg
    assert leg["distributions"] == ["frobnicator"], leg
    assert leg["interpreter"] == "/usr/bin/python3.14", leg
    # The UNDECLARED import error is an ordinary gate failure, never folded into the leg.
    assert not any("undeclared" in cmd for cmd in leg["gates"]), leg["gates"]
    assert all("declared_" in cmd for cmd in leg["gates"]), leg["gates"]

def test_arm_b2_only_failed_gates_are_classified() -> None:
    """A PASSING or UNKNOWN row is never evidence of a missing module, whatever its stderr says."""
    mod = _load_audit()
    declared = mod.read_declared_distributions()
    noisy = "ModuleNotFoundError: No module named 'aiosqlite'"
    rows = [
        {"cmd": "passing", "passed": True, "unknown": False, "stderr": noisy},
        {"cmd": "unknown", "passed": False, "unknown": True, "stderr": noisy},
    ]
    assert mod.classify_environment_failures(rows, interpreter="x") is None, rows
    rows.append({"cmd": "failed", "passed": False, "unknown": False, "stderr": noisy})
    leg = mod.classify_environment_failures(rows, interpreter="x")
    assert leg is not None and leg["gates"] == ["failed"], leg
    assert "aiosqlite" in declared, declared

def test_arm_b3_the_leg_is_one_line_naming_interpreter_and_distributions() -> None:
    """The stdout surface: a SINGLE line, not N rows and not a paragraph."""
    mod = _load_audit()
    results, pyproject = _synthetic_run(mod)
    leg = mod.classify_environment_failures(
        results, interpreter="/usr/bin/python3.14", pyproject_path=pyproject
    )
    assert leg is not None
    line = mod.render_environment_leg_line(leg)
    assert "\n" not in line, line
    assert "/usr/bin/python3.14" in line, line
    assert "frobnicator" in line, line
    assert "2 gate(s)" in line, line

# --- Arm C: the rendered scorecard --------------------------------------------------

STUB_STATS = {
    "ledger": {"total_events": 32, "closed_tasks": 5, "run_events": 12, "runs_by_outcome": {}},
    "rework": {"total_entries": 3, "rework_rate": 0.75},
    "cadence": {"cadence_held": True, "hours_since_last_run": 24.0},
}

def _render(mod: ModuleType, results: list[dict[str, Any]], leg: dict[str, Any] | None) -> str:
    """The REAL report renderer, driven by a fixture instant (never a claim about the world)."""
    return mod.format_report_markdown(
        "2026-11-25",
        "2026-11-25T00:00:00Z",
        STUB_STATS["ledger"],
        STUB_STATS["rework"],
        STUB_STATS["cadence"],
        results,
        environment_leg=leg,
    )

def _leg_present(mod: ModuleType, results: list[dict[str, Any]], leg: dict[str, Any]) -> bool:
    """THE predicate Arms C and D share: does the scorecard carry the leg, once, above 2.1?"""
    body = _render(mod, results, leg)
    if body.count("### 2.0 Environment leg") != 1:
        return False
    if leg["interpreter"] not in body:
        return False
    if not all(name in body for name in leg["distributions"]):
        return False
    return body.index("### 2.0 Environment leg") < body.index("### 2.1 Failing gate output")

def test_arm_c_scorecard_carries_the_leg_once_and_still_fails_the_rows() -> None:
    """ONE leg above the rows, and the rows it explains are still FAIL in the same artifact."""
    mod = _load_audit()
    results, pyproject = _synthetic_run(mod)
    leg = mod.classify_environment_failures(
        results, interpreter="/usr/bin/python3.14", pyproject_path=pyproject
    )
    assert leg is not None
    assert _leg_present(mod, results, leg), "the scorecard does not carry the classified leg"
    body = _render(mod, results, leg)
    # NOT LAUNDERED: the leg names a cause, it does not turn the rows green.
    verdict = mod.judge_gates(results)
    assert verdict.status == "DEGRADED", verdict.status
    assert verdict.healthy is False
    assert body.count("| `FAIL` |") == 3, body[body.index("## 2."):][:1200]

def test_arm_c2_no_leg_no_section() -> None:
    """The control: without a classified leg the section is absent -- so its presence is the leg."""
    mod = _load_audit()
    results, _pyproject = _synthetic_run(mod)
    body = _render(mod, results, None)
    assert "### 2.0 Environment leg" not in body

# --- Arm D: neuter ------------------------------------------------------------------

LEG_RENDER = '    if environment_leg:\n        lines.extend([""] + render_environment_leg_markdown(environment_leg))\n'

def _neuter_render_copy(root: Path) -> Path:
    """A REAL copy of the audit tree with the leg's render call removed; returns its path."""
    tools = root / "tools"
    tools.mkdir(parents=True)
    for sibling in ("audit.py", "field_predicate.py", "gate_budget.py"):
        shutil.copyfile(REPO / "tools" / sibling, tools / sibling)
    copy = tools / "audit.py"
    source = copy.read_text(encoding="utf-8")
    assert source.count(LEG_RENDER) == 1, (
        "neuter target missing: the leg's render call is no longer a single block -- "
        "update this gate's LEG_RENDER rather than deleting the arm"
    )
    copy.write_text(source.replace(LEG_RENDER, "", 1), encoding="utf-8")
    return copy

def test_arm_d_neuter_removing_the_render_call_reds_arm_c() -> None:
    """Same predicate as Arm C, on a copy whose render call is gone: it must go RED."""
    with tempfile.TemporaryDirectory() as tmp:
        neutered = _load_audit(_neuter_render_copy(Path(tmp)), name="audit_env_neutered")
        results, pyproject = _synthetic_run(neutered)
        leg = neutered.classify_environment_failures(
            results, interpreter="/usr/bin/python3.14", pyproject_path=pyproject
        )
        assert leg is not None, "the neutered copy lost the classifier too -- widen the neuter scope"
        assert not _leg_present(neutered, results, leg), (
            "neuter proof failed: the scorecard still carries the leg with its render call "
            "removed, so Arm C is not measuring that call"
        )

def _run_as_script() -> int:
    failures = 0
    for name, fn in [
        ("arm_a", test_arm_a_real_pyproject_declares_the_incident_distributions),
        ("arm_a2", test_arm_a2_names_normalize_and_every_group_is_read),
        ("arm_b", test_arm_b_declared_failures_collapse_to_one_leg),
        ("arm_b2", test_arm_b2_only_failed_gates_are_classified),
        ("arm_b3", test_arm_b3_the_leg_is_one_line_naming_interpreter_and_distributions),
        ("arm_c", test_arm_c_scorecard_carries_the_leg_once_and_still_fails_the_rows),
        ("arm_c2", test_arm_c2_no_leg_no_section),
        ("arm_d", test_arm_d_neuter_removing_the_render_call_reds_arm_c),
    ]:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
    print("0 failed" if failures == 0 else f"{failures} failed")
    return 1 if failures else 0

if __name__ == "__main__":
    sys.exit(_run_as_script())
