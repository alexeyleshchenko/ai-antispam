"""
Static audit: referenced t() keys vs locale YAML string leaves.

Dev/test tooling only (not part of runtime app package). Used by
scripts/audit_i18n.py and tests. Imports app.i18n for HELP_PAGE_CALLBACK_KEYS
and _get_nested; does not import handlers.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.i18n import HELP_PAGE_CALLBACK_KEYS, _get_nested

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_APP_ROOT = _PROJECT_ROOT / "src" / "app"
_LOCALES_DIR = _APP_ROOT / "locales"

# bot_commands.py: _COMMAND_IDS — expand t(lang, f"bot_commands.{cmd}")
_BOT_COMMAND_IDS = ("start", "help", "buy", "stats", "mode", "ref", "lang")

# Keys passed as variables; not visible as string literals in t() calls
_KNOWN_DYNAMIC_KEYS = frozenset(
    {
        "spam.permission_delete_action",
        "spam.permission_ban_action",
        "spam.destroyed_channel",
        "spam.destroyed_user",
        "status.permission_steps_public",
        "status.permission_steps_private",
        "spam.entity_channel",
        "spam.entity_group",
    }
)


def _flatten_string_keys(data: Any, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    if not isinstance(data, dict):
        return keys
    for k, v in data.items():
        path = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, str):
            keys.add(path)
        elif isinstance(v, dict):
            keys |= _flatten_string_keys(v, path)
    return keys


def _expand_joinedstr_bot_commands(node: ast.JoinedStr) -> set[str] | None:
    if len(node.values) != 2:
        return None
    a, b = node.values
    if not isinstance(a, ast.Constant) or a.value != "bot_commands.":
        return None
    if not isinstance(b, ast.FormattedValue) or not isinstance(b.value, ast.Name):
        return None
    if b.value.id != "cmd":
        return None
    return {f"bot_commands.{c}" for c in _BOT_COMMAND_IDS}


def extract_t_keys_from_file(path: Path) -> tuple[set[str], list[tuple[int, str]]]:
    """Return (static_keys, (lineno, expr) for dynamic second args)."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, SyntaxError):
        return set(), []

    class TCallVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.static: set[str] = set()
            self.dynamic: list[tuple[int, str]] = []

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "t":
                if len(node.args) >= 2:
                    arg = node.args[1]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        self.static.add(arg.value)
                    elif isinstance(arg, ast.JoinedStr):
                        expanded = _expand_joinedstr_bot_commands(arg)
                        if expanded is not None:
                            self.static |= expanded
                        else:
                            self.dynamic.append((node.lineno, ast.unparse(arg)))
                    else:
                        self.dynamic.append((node.lineno, ast.unparse(arg)))
            self.generic_visit(node)

    vis = TCallVisitor()
    vis.visit(tree)
    return vis.static, vis.dynamic


def _collect_py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if p.is_file())


@dataclass
class I18nKeysAuditResult:
    referenced_keys: set[str]
    """Keys we expect to resolve to a string in each locale."""

    missing_by_lang: dict[str, list[str]]
    """lang -> sorted keys missing or non-string in that locale."""

    dynamic_t_calls: list[tuple[str, int, str]]
    """(relative_path, lineno, second_arg_ast) for non-literal t() keys."""

    locale_yaml_keys: dict[str, set[str]] = field(default_factory=dict)
    """lang -> all string leaf paths in that YAML."""


def run_i18n_keys_audit(
    *,
    app_pkg: Path | None = None,
    locales_dir: Path | None = None,
) -> I18nKeysAuditResult:
    """
    Load en/ru YAML, scan app_pkg for t() calls, merge HELP_PAGE_CALLBACK_KEYS
    and known dynamic keys, return missing keys per language.
    """
    pkg = app_pkg or _APP_ROOT
    loc_dir = locales_dir or _LOCALES_DIR

    locale_data: dict[str, dict[str, Any]] = {}
    locale_keys: dict[str, set[str]] = {}
    for lang in ("en", "ru"):
        path = loc_dir / f"{lang}.yaml"
        with open(path, encoding="utf-8") as f:
            locale_data[lang] = yaml.safe_load(f) or {}
        locale_keys[lang] = _flatten_string_keys(locale_data[lang])

    used: set[str] = set(HELP_PAGE_CALLBACK_KEYS) | set(_KNOWN_DYNAMIC_KEYS)
    dynamic: list[tuple[str, int, str]] = []

    for py in _collect_py_files(pkg):
        keys, dyn = extract_t_keys_from_file(py)
        used |= keys
        rel = str(py.relative_to(pkg))
        for line, expr in dyn:
            dynamic.append((rel, line, expr))

    missing: dict[str, list[str]] = {lang: [] for lang in ("en", "ru")}
    for lang in ("en", "ru"):
        data = locale_data[lang]
        for key in sorted(used):
            val = _get_nested(data, key)
            if val is None or not isinstance(val, str):
                missing[lang].append(key)

    return I18nKeysAuditResult(
        referenced_keys=used,
        missing_by_lang=missing,
        dynamic_t_calls=dynamic,
        locale_yaml_keys=locale_keys,
    )


def unused_yaml_keys(result: I18nKeysAuditResult) -> dict[str, list[str]]:
    """YAML string keys not in referenced_keys (heuristic)."""
    return {
        lang: sorted(result.locale_yaml_keys[lang] - result.referenced_keys)
        for lang in ("en", "ru")
    }
