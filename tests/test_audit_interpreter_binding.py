"""Gate: the audit's gates bind to an explicit interpreter, and the scorecard names it.

Why this file exists
--------------------
#158: `tools/audit.py` used to launch its gates with the ambient `sys.executable`
and its scorecard never said which interpreter ran them. A host alternatives flip
(2026-11-20) turned 16 gates red with `No module named aiosqlite`, and the scorecard
gave no way to see the interpreter that had produced the verdict.

This gate checks three things:

1. Arm A -- the scorecard header names an interpreter version (`sys.version`) and
   the resolved GATE_PYTHON path.
2. Arm B -- resolution prefers an explicit `AUDIT_GATE_PYTHON` override over the
   pinned path and the ambient interpreter.
3. Arm D -- the RENDERED header (clause 1's stated shape): it carries a
   `\\d+\\.\\d+\\.\\d+` version and an absolute resolved path, and a stubbed different
   version renders a DIFFERENT header. Arms A and C read the source; this one reads the
   artifact.

Neuter proof (Arm C): removing the interpreter line from the header source makes
Arm A's check fail. A check that still passes with its subject removed proves
nothing, so Arm C runs the same predicate on a mutated copy of the source.

Runs two ways: as a script (`python3 tests/test_audit_interpreter_binding.py`) and
under pytest. Run it under the pinned interpreter:
`/usr/bin/python3.14 tests/test_audit_interpreter_binding.py`.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
AUDIT_PATH = ROOT / "tools" / "audit.py"

# The header check: the scorecard's interpreter line must name BOTH the running
# version and the resolved gate path. It takes a PATH so the neuter arm walks the
# identical route (read file -> check) that Arm A walks.
# The block is the f-string across THREE source lines, ending at the closing `",`
# of the last line (the GATE_PYTHON segment). A two-line match never reaches it.
INTERPRETER_LINE_RE = re.compile(r'f"> \*\*Interpreter:\*\*[\s\S]*?",\n')


def _header_names_interpreter(path: Path) -> bool:
    """True when the scorecard header at `path` names the version and the gate path."""
    source = path.read_text(encoding="utf-8")
    m = INTERPRETER_LINE_RE.search(source)
    if not m:
        return False
    block = m.group(0)
    return "sys.version" in block and "GATE_PYTHON" in block


def _load_audit() -> ModuleType:
    # audit.py's @dataclass decorators resolve cls.__module__ through sys.modules,
    # so the module must be registered BEFORE exec_module runs.
    spec = importlib.util.spec_from_file_location("audit_under_test", AUDIT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_under_test"] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop("audit_under_test", None)
        raise
    return mod


def _reset_cache(mod: ModuleType) -> None:
    mod._GATE_PYTHON_CACHE.clear()


def _assert_header_names_interpreter(path: Path) -> None:
    """The assertion both Arm A and its neuter run, so the neuter proves THAT check."""
    assert _header_names_interpreter(path), (
        f"scorecard header at {path} must name sys.version and GATE_PYTHON (#158)"
    )


def test_arm_a_header_names_interpreter_version() -> None:
    _assert_header_names_interpreter(AUDIT_PATH)



OVERRIDE_CANDIDATE = "/root/ai-antispam/.venv/bin/python"


def test_arm_b_override_beats_pinned_and_ambient() -> None:
    """The explicit override wins over the pinned path, and it is a DIFFERENT path.

    The discriminator matters: an override equal to the pinned path proves nothing,
    because the pin would have produced the same answer.
    """
    mod = _load_audit()
    prior = os.environ.get("AUDIT_GATE_PYTHON")
    try:
        os.environ["AUDIT_GATE_PYTHON"] = OVERRIDE_CANDIDATE
        _reset_cache(mod)
        chosen, _causes = mod.resolve_gate_python()
        assert chosen == OVERRIDE_CANDIDATE, (
            f"AUDIT_GATE_PYTHON must win; resolved {chosen!r}, expected {OVERRIDE_CANDIDATE!r}"
        )
        assert chosen != mod.GATE_PYTHON_PINNED, (
            "override equals the pinned path -- this arm cannot discriminate"
        )
    finally:
        if prior is None:
            os.environ.pop("AUDIT_GATE_PYTHON", None)
        else:
            os.environ["AUDIT_GATE_PYTHON"] = prior
        _reset_cache(mod)


def test_arm_b2_bogus_override_falls_through_and_is_named() -> None:
    """A bad override must not be used, and its cause must be reported."""
    mod = _load_audit()
    prior = os.environ.get("AUDIT_GATE_PYTHON")
    try:
        os.environ["AUDIT_GATE_PYTHON"] = "/nonexistent/python-does-not-exist"
        _reset_cache(mod)
        chosen, causes = mod.resolve_gate_python()
        assert chosen is not None, "a bogus override must fall through to a working interpreter"
        assert chosen != "/nonexistent/python-does-not-exist"
        assert any("/nonexistent/python-does-not-exist" in c for c in causes), (
            f"the rejected override must be named in the causes; got {causes}"
        )
    finally:
        if prior is None:
            os.environ.pop("AUDIT_GATE_PYTHON", None)
        else:
            os.environ["AUDIT_GATE_PYTHON"] = prior
        _reset_cache(mod)


VERSION_SEGMENT = "sys.version `{sys.version.split()[0]}` · "


def test_arm_c_neuter_removing_header_line_reds_arm_a() -> None:
    """Strip the version segment from a REAL copy on disk, then run Arm A's predicate on it.

    Same route as Arm A (read file -> _header_names_interpreter), so the neuter proves
    that check and not some parallel one.
    """
    source = AUDIT_PATH.read_text(encoding="utf-8")
    assert VERSION_SEGMENT in source, "neuter target missing: header no longer has the version segment"
    mutated = source.replace(VERSION_SEGMENT, "", 1)
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "audit_neutered.py"
        copy.write_text(mutated, encoding="utf-8")
        assert not _header_names_interpreter(copy), (
            "neuter proof failed: header predicate still passes with sys.version removed"
        )


# --- Arm D: the RENDERED header (clause 1's stated shape) ---------------------------
#
# Arms A and C read the SOURCE. Clause 1's close condition asks for the RENDERED header: a
# `\d+\.\d+\.\d+` version and an absolute resolved path, and a DIFFERENT header when a
# different version is rendered. A source check cannot see a formatter that drops the value,
# and the 2026-11-20 incident was precisely an artifact whose field named the symlink under
# both arms -- so the claim is measured on the artifact, through the real formatter.

# Fixture values for the formatter's non-interpreter inputs; this arm asserts ONE line and
# never a figure, so the stats are stubs by design, not claims about the world.
STUB_LEDGER = {"total_events": 32, "closed_tasks": 5, "run_events": 12, "runs_by_outcome": {}}
STUB_REWORK = {"total_entries": 3, "rework_rate": 0.75}
STUB_CADENCE = {"cadence_held": True, "hours_since_last_run": 24.0}
VERSION_RE = re.compile(r"\d+\.\d+\.\d+")

class _SysProxy:
    """The audit module's `sys`, with named attributes overridden.

    A proxy rather than a patch of the real `sys` module: the stub is confined to the module
    under test, so pytest and the rest of this process keep the true interpreter for the
    window in which the fake one is rendered.
    """

    def __init__(self, base: ModuleType, **overrides: object) -> None:
        self._base = base
        self._overrides = overrides

    def __getattr__(self, name: str) -> object:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._base, name)

def _rendered_header(mod: ModuleType) -> str:
    """The scorecard's Interpreter line, rendered by the REAL formatter."""
    body = mod.format_report_markdown(
        "2026-11-25", "2026-11-25T00:00:00Z",
        dict(STUB_LEDGER), dict(STUB_REWORK), dict(STUB_CADENCE), [],
    )
    for line in body.splitlines():
        if line.startswith("> **Interpreter:**"):
            return line
    raise AssertionError("the rendered scorecard carries no `> **Interpreter:**` line")

def test_arm_d_rendered_header_names_version_and_resolved_path() -> None:
    """The RENDERED line: a `\\d+\\.\\d+\\.\\d+` version and an ABSOLUTE resolved path."""
    mod = _load_audit()
    line = _rendered_header(mod)
    assert VERSION_RE.search(line), f"no version in the rendered header: {line!r}"
    assert sys.version.split()[0] in line, f"the running version is not named: {line!r}"
    resolved = os.path.realpath(sys.executable)
    assert os.path.isabs(resolved), f"realpath is not absolute: {resolved!r}"
    assert resolved in line, f"the resolved interpreter path is not named: {line!r}"
    gate_python_path = mod.resolve_gate_python()[0]
    assert gate_python_path and os.path.isabs(gate_python_path), gate_python_path
    assert gate_python_path in line, f"the resolved gate interpreter is not named: {line!r}"

def test_arm_d2_a_stubbed_version_renders_a_different_header() -> None:
    """A run under a different version is DISTINGUISHABLE by reading the artifact alone."""
    mod = _load_audit()
    real_line = _rendered_header(mod)
    stub_version = "3.99.0 (stub, #158 arm D2)"
    prior_sys = mod.sys
    mod.sys = _SysProxy(sys, version=stub_version)
    try:
        stubbed_line = _rendered_header(mod)
    finally:
        mod.sys = prior_sys
    assert stubbed_line != real_line, (
        "a stubbed different version rendered the SAME header -- the artifact cannot "
        f"distinguish the arms: {real_line!r}"
    )
    assert "3.99.0" in stubbed_line, stubbed_line
    assert VERSION_RE.search(stubbed_line), stubbed_line

def _run_as_script() -> int:
    failures = 0
    for name, fn in [
        ("arm_a", test_arm_a_header_names_interpreter_version),
        ("arm_b", test_arm_b_override_beats_pinned_and_ambient),
        ("arm_b2", test_arm_b2_bogus_override_falls_through_and_is_named),
        ("arm_c", test_arm_c_neuter_removing_header_line_reds_arm_a),
        ("arm_d", test_arm_d_rendered_header_names_version_and_resolved_path),
        ("arm_d2", test_arm_d2_a_stubbed_version_renders_a_different_header),
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