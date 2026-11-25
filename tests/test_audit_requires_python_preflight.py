#!/usr/bin/env python3
"""Gate: the audit REFUSES to run under an interpreter that violates requires-python.

Why this file exists
--------------------
#158 clause 3. On 2026-11-20 a host alternatives flip pointed `python3` at 3.12 while
`pyproject.toml:12` declares `requires-python = ">=3.14"`. The daily self-audit then ran its
whole gate suite anyway and published 16 failures -- 15 `ModuleNotFoundError: aiosqlite` and
one `SyntaxError` from PEP 758 syntax that parses on 3.14 and has been in the file since
2026-09-29. Every one of those verdicts described the interpreter, not the tree, and nothing
on the artifact said so.

The fix is a PREFLIGHT: `tools/audit.py::main()` compares the running `sys.version_info`
against the repo's own declaration and, on a mismatch, prints a refusal that quotes the
declaration and names the interpreter, exits 2, runs NO gate and writes NO scorecard.

Arms
----
A  SATISFIED -- the real pyproject declaration is read verbatim, the running interpreter
   satisfies it, and `main()` therefore launches the gates instead of refusing.
B  REFUSES   -- a declaration the running interpreter does NOT satisfy (stubbed at the read,
   so no second real interpreter is needed on the box) makes `main()` exit 2, quote the
   declaration, name the interpreter, run no gate and leave no scorecard behind.
C  NEUTER    -- Arm B's predicate re-run against a REAL copy of `tools/audit.py` on disk with
   the preflight call removed: it must go RED, so Arm B proves the WIRING and not merely the
   existence of a helper nobody calls.

Runs two ways, matching the sibling gates: as a script
(`/usr/bin/python3.14 tests/test_audit_requires_python_preflight.py`, the form `__main__`
owns) and under pytest, which collects the `test_*` wrappers. Run it under the pinned
interpreter -- this gate's own subject is the interpreter, so a bare `python3` launch would
test the host's alternatives link.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent
AUDIT_PATH = REPO / "tools" / "audit.py"
PYPROJECT = REPO / "pyproject.toml"

# The refusal must quote the DECLARATION it violated, and this repo's is a fixed fact. Pinned
# so a change to pyproject.toml makes this gate speak rather than silently re-baselining.
EXPECTED_DECLARATION = ">=3.14"
# A declaration no interpreter satisfies: the refuses arm needs a MISMATCH, and driving one
# through the read keeps the test independent of whichever interpreter runs it.
UNSATISFIABLE = ">=3.99"

class _GatesRan(Exception):
    """Raised by the gate stub when `main()` reached the gate suite -- i.e. did NOT refuse."""

def _load_audit(path: Path = AUDIT_PATH, name: str = "audit_preflight_under_test") -> ModuleType:
    """Load an audit module from `path`.

    `audit.py`'s `@dataclass` decorators resolve `cls.__module__` through `sys.modules`, so
    the module must be registered BEFORE `exec_module` runs. The sibling imports
    (`field_predicate`, `gate_budget`) resolve through the file's own directory, which is why
    the neuter copy below is written into a temp `tools/` beside copies of both.
    """
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

def _drive_main(
    mod: ModuleType,
    argv: list[str],
    declared: str | None,
    gate_stub,
) -> tuple[object, str, str]:
    """Run `mod.main()` with the declaration read and the gate suite STUBBED.

    Returns `(rc, stdout, stderr)`. `rc` is `"gates-ran"` when the gate stub's `_GatesRan`
    escaped, which is the honest way to observe "the preflight did not refuse": the suite was
    reached, so nothing stopped it. Both stubs are restored in a `finally`, because a leaked
    stub would make every later arm in this process a measurement of the stub.
    """
    prior_argv = sys.argv
    prior_read = mod.read_declared_requires_python
    prior_gates = mod.execute_mechanical_gates
    sys.argv = argv
    mod.read_declared_requires_python = lambda *a, **k: declared
    mod.execute_mechanical_gates = gate_stub
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc: object = mod.main()
            except _GatesRan:
                rc = "gates-ran"
    finally:
        sys.argv = prior_argv
        mod.read_declared_requires_python = prior_read
        mod.execute_mechanical_gates = prior_gates
    return rc, out.getvalue(), err.getvalue()

def _refusal_under_unsatisfiable(mod: ModuleType) -> tuple[object, str, str]:
    """THE predicate Arms B and C share: does this module REFUSE the unsatisfiable run?"""
    def gates_ran(_repo_root):  # noqa: ANN001 - the audit's own signature is positional
        raise _GatesRan

    return _drive_main(mod, ["audit.py"], UNSATISFIABLE, gates_ran)

# --- Arm A: satisfied -------------------------------------------------------------

def test_arm_a_real_declaration_is_read_and_satisfied() -> None:
    """The declaration is read VERBATIM from pyproject.toml, and this interpreter satisfies it."""
    mod = _load_audit()
    declared = mod.read_declared_requires_python()
    assert declared == EXPECTED_DECLARATION, (
        f"pyproject.toml must declare requires-python {EXPECTED_DECLARATION!r}; read {declared!r}"
    )
    assert sys.version_info[:3] >= (3, 14, 0), (
        f"this gate must run under the declared interpreter; running {sys.version.split()[0]}"
    )
    assert mod.requires_python_violation(declared) is None, (
        "a satisfying declaration must produce NO violation"
    )

def test_arm_a2_satisfied_run_launches_the_gates() -> None:
    """Under the REAL declaration `main()` does not refuse: the gate suite is reached.

    The ledger legs are STUBBED and that is a scope statement, not a shortcut: the claim here
    is about the PREFLIGHT's position -- a satisfying declaration must not stop the run before
    the gates -- and parsing 4,500 ledger rows twice would cost this gate more wall than every
    other arm together while testing `parse_ledger`, which is not its subject. The declaration
    read is deliberately NOT stubbed: it is the input under test.
    """
    mod = _load_audit()
    called: list[bool] = []

    def gates_reached(_repo_root) -> list:  # noqa: ANN001
        called.append(True)
        return []

    def ledger_stub(_path) -> tuple[dict, set]:  # noqa: ANN001
        return {}, set()

    def rework_stub(_path, _closed) -> dict:  # noqa: ANN001
        return {}

    def cadence_stub(_ledger, _scores, own_run_date=None) -> dict:  # noqa: ANN001
        return {"cadence_held": True, "hours_since_last_run": 24.0, "record_read": "stub",
                "artifact_latest_date": "stub"}

    def tree_stub(_root) -> dict:  # noqa: ANN001
        return {"head_sha": None, "modified_count": 0, "modified_paths": []}

    prior_argv = sys.argv
    stubs = {
        "execute_mechanical_gates": gates_reached,
        "parse_ledger": ledger_stub,
        "parse_rework": rework_stub,
        "check_cadence_integrity": cadence_stub,
        "tree_condition": tree_stub,
    }
    prior = {name: getattr(mod, name) for name in stubs}
    sys.argv = ["audit.py", "--json"]
    for name, value in stubs.items():
        setattr(mod, name, value)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = mod.main()
    finally:
        sys.argv = prior_argv
        for name, value in prior.items():
            setattr(mod, name, value)
    assert called, "the gate suite was never reached -- the preflight refused a satisfying run"
    assert "AUDIT REFUSED" not in err.getvalue(), err.getvalue()
    assert rc == 0, f"main() returned {rc!r} on a stubbed healthy run; stderr {err.getvalue()[:200]!r}"
    assert '"gates_skipped": false' in out.getvalue(), out.getvalue()[:200]

# --- Arm B: refuses ---------------------------------------------------------------

def test_arm_b_unsatisfiable_declaration_refuses() -> None:
    """A declaration this interpreter violates: exit 2, and the gate suite is never reached."""
    mod = _load_audit()
    rc, out, err = _refusal_under_unsatisfiable(mod)
    assert rc == 2, f"an unsatisfiable declaration must refuse with exit 2; got {rc!r} (stdout {out[:200]!r})"
    assert "AUDIT REFUSED" in err, err

def test_arm_b2_refusal_quotes_declaration_and_names_interpreter() -> None:
    """The refusal's two load-bearing facts: the QUOTED declaration and the RUNNING interpreter."""
    mod = _load_audit()
    _rc, _out, err = _refusal_under_unsatisfiable(mod)
    assert f'"{UNSATISFIABLE}"' in err, f"the refusal must QUOTE the declaration; got {err!r}"
    assert sys.version.split()[0] in err, (
        f"the refusal must name the running version {sys.version.split()[0]!r}; got {err!r}"
    )
    assert os.path.realpath(sys.executable) in err, (
        f"the refusal must name the RESOLVED interpreter {os.path.realpath(sys.executable)!r}"
    )

def test_arm_b3_refusal_writes_no_scorecard_and_runs_no_gate() -> None:
    """The refusal is the WHOLE run: no scorecard on disk, no gate launched."""
    mod = _load_audit()
    with tempfile.TemporaryDirectory() as tmp:
        card = Path(tmp) / "scorecard.md"
        called: list[bool] = []

        def gates_reached(_repo_root) -> list:  # noqa: ANN001
            called.append(True)
            return []

        rc, _out, err = _drive_main(
            mod, ["audit.py", "--report", "--output", str(card)], UNSATISFIABLE, gates_reached
        )
        assert rc == 2, f"expected the refusal's exit 2; got {rc!r}"
        assert "AUDIT REFUSED" in err, err
        assert not called, "a gate was launched despite the refusal"
        assert not card.exists(), f"a scorecard was written despite the refusal: {card}"

# --- Arm C: neuter ------------------------------------------------------------------

PREFLIGHT_CALL = "    _requires_python_violation = requires_python_violation(_declared_requires_python)\n"

def _neuter_preflight_copy(root: Path) -> Path:
    """A REAL copy of the audit tree with the preflight call removed; returns its audit path."""
    tools = root / "tools"
    tools.mkdir(parents=True)
    for sibling in ("audit.py", "field_predicate.py", "gate_budget.py"):
        shutil.copyfile(REPO / "tools" / sibling, tools / sibling)
    copy = tools / "audit.py"
    source = copy.read_text(encoding="utf-8")
    assert source.count(PREFLIGHT_CALL) == 1, (
        "neuter target missing: the preflight call is no longer a single line -- "
        "update this gate's PREFLIGHT_CALL rather than deleting the arm"
    )
    copy.write_text(
        source.replace(PREFLIGHT_CALL, "    _requires_python_violation = None  # NEUTERED\n", 1),
        encoding="utf-8",
    )
    return copy

def test_arm_c_neuter_removing_preflight_reds_arm_b() -> None:
    """Same predicate as Arm B, on a copy whose preflight call is gone: it must go RED."""
    with tempfile.TemporaryDirectory() as tmp:
        neutered = _load_audit(_neuter_preflight_copy(Path(tmp)), name="audit_preflight_neutered")
        rc, _out, err = _refusal_under_unsatisfiable(neutered)
        assert rc == "gates-ran", (
            "neuter proof failed: the copy still refused the unsatisfiable run, so Arm B is "
            f"not measuring the preflight (rc={rc!r}, stderr={err[:200]!r})"
        )

def _run_as_script() -> int:
    failures = 0
    for name, fn in [
        ("arm_a", test_arm_a_real_declaration_is_read_and_satisfied),
        ("arm_a2", test_arm_a2_satisfied_run_launches_the_gates),
        ("arm_b", test_arm_b_unsatisfiable_declaration_refuses),
        ("arm_b2", test_arm_b2_refusal_quotes_declaration_and_names_interpreter),
        ("arm_b3", test_arm_b3_refusal_writes_no_scorecard_and_runs_no_gate),
        ("arm_c", test_arm_c_neuter_removing_preflight_reds_arm_b),
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
