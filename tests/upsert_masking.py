"""#146 — enumerate every `ON CONFLICT ... DO UPDATE` upsert in `src/`.

`tests/conftest.py::_convert_on_conflict_to_sqlite` used to rewrite every
`INSERT ... ON CONFLICT ... DO UPDATE` that lacked `RETURNING` into
`INSERT OR REPLACE`, which is delete + re-insert: the SET clause is discarded
and every column the INSERT list omits resets to its schema default. A test
driving such a branch therefore certified behaviour production never has — #41
(a paying customer's groups silently unmoderated for ~36 days) and #130
(`is_registered` lost on procedure rows) are the defects that trap was shaped to
hide.

The rewrite is gone, and `tests/test_upsert_adapter_faithful.py` is what keeps
it gone. This module only ENUMERATES; it deliberately does not decide which
sites are masked. An earlier version did — it mirrored the adapter's
`RETURNING` check by hand — and that copy is exactly how an instrument drifts
from the code it measures: fixing the adapter would have left the enumerator
still reporting nine masked sites, so a guard built on it would have been green
against a broken tree. The guard asks the adapter itself.

The population is derived from the TREE, by AST, never from a hand-maintained
table: a table goes stale on the next refactor, and a stale enumeration is the
"clean zero from a broken instrument" failure this exists to prevent.
Enumerating by AST rather than by path:line means a moved function stays
covered.

Only string literals that actually reach the database are counted: arguments to
`execute`/`fetchrow`/`fetchval`/`fetch`/`executemany`, plus simple `sql = "..."`
local assignments. Docstrings and comments are excluded by construction — a
docstring in this repo contains both phrases, and a naive `grep -c 'DO UPDATE'`
counts it.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "app"

#: Methods whose first positional argument is SQL.
_DB_METHODS = frozenset({"execute", "fetchrow", "fetchval", "fetch", "executemany"})

#: Local variable names treated as SQL (simple `sql = "..."` assignments).
_SQL_VAR = re.compile(r"(sql|query|stmt|statement)$", re.IGNORECASE)

_DO_UPDATE = re.compile(
    r"\bON\s+CONFLICT\b.*?\bDO\s+UPDATE\b", re.IGNORECASE | re.DOTALL
)


@dataclass(frozen=True)
class UpsertSite:
    """One `ON CONFLICT ... DO UPDATE` literal found in `src/`."""

    module: str
    function: str
    lineno: int
    sql: str

    @property
    def where(self) -> str:
        return f"{self.module}:{self.lineno} ({self.function})"


def _enclosing_functions(tree: ast.AST) -> dict[int, str]:
    """Map every node id to the name of its innermost enclosing function."""
    owner: dict[int, str] = {}

    def walk(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = current
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            owner[id(child)] = name
            walk(child, name)

    owner[id(tree)] = "<module>"
    walk(tree, "<module>")
    return owner


def _flatten(node: ast.AST) -> tuple[str, ast.AST] | None:
    """SQL text and line number for a string constant OR an f-string.

    f-strings matter: `database_schema.create_procedures` passes its DDL as an
    `f\"\"\"...\"\"\"`, which is an `ast.JoinedStr`, not an `ast.Constant`. An
    enumerator that only accepts constants silently drops it — and a masked
    site missing from the population is exactly the "clean zero from a broken
    instrument" failure this module exists to prevent (measured 2026-11-17:
    constants-only reported 8 where the tree carries 9).

    Formatted values are replaced by a placeholder. A keyword split across an
    interpolation could in principle still match; none does today, and the
    alternative — skipping f-strings — under-counts, which is the worse error.
    """
    if isinstance(node, ast.Constant):
        return (node.value, node) if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for piece in node.values:
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                parts.append(piece.value)
            else:
                parts.append("\x00")  # interpolation placeholder
        return "".join(parts), node
    return None


def _sql_literals(tree: ast.AST) -> list[tuple[str, ast.AST]]:
    """Every string literal that plausibly reaches the database."""
    found: list[tuple[str, ast.AST]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            )
            if name in _DB_METHODS and node.args:
                flat = _flatten(node.args[0])
                if flat:
                    found.append(flat)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            flat = _flatten(node.value) if node.value is not None else None
            if not flat:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and _SQL_VAR.search(target.id):
                    found.append(flat)
    return found


def enumerate_upsert_sites(src_root: Path | None = None) -> list[UpsertSite]:
    """Every `DO UPDATE` upsert literal in `src/`, carrying its own text."""
    root = src_root or SRC_ROOT
    sites: list[UpsertSite] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - a syntax error is its own failure
            continue
        owner = _enclosing_functions(tree)
        for text, node in _sql_literals(tree):
            if not _DO_UPDATE.search(text):
                continue
            sites.append(
                UpsertSite(
                    module=str(path.relative_to(root.parent.parent)),
                    function=owner.get(id(node), "<module>"),
                    lineno=node.lineno,
                    sql=text,
                )
            )
    return sites


if __name__ == "__main__":  # manual recon: `python3 tests/upsert_masking.py`
    sites = enumerate_upsert_sites()
    print(f"DO UPDATE sites in src/: {len(sites)}")
    for site in sites:
        print(f"  {site.where}")
