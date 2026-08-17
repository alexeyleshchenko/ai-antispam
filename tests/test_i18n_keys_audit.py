"""Static checks: referenced t() keys and help callbacks exist as strings in en/ru YAML."""

import yaml

from tests.support.i18n_keys_audit import run_i18n_keys_audit

LOCALE_PATHS = [
    "src/app/locales/ru.yaml",
    "src/app/locales/en.yaml",
]


def _walk_strings(node, path, out):
    """Collect (path, value) for every string leaf in a parsed YAML doc."""
    if isinstance(node, dict):
        for k, v in node.items():
            _walk_strings(v, f"{path}.{k}" if path else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_strings(v, f"{path}[{i}]", out)
    elif isinstance(node, str):
        out.append((path, node))


def test_all_referenced_i18n_keys_exist_in_locales() -> None:
    """
    AST scan of app/, plus HELP_PAGE_CALLBACK_KEYS and known dynamic keys,
    must resolve to string leaves in both en.yaml and ru.yaml.
    """
    result = run_i18n_keys_audit()
    assert not result.missing_by_lang["en"], (
        "Missing or non-string in en.yaml:\n  "
        + "\n  ".join(result.missing_by_lang["en"])
    )
    assert not result.missing_by_lang["ru"], (
        "Missing or non-string in ru.yaml:\n  "
        + "\n  ".join(result.missing_by_lang["ru"])
    )


def test_no_literal_backslash_n_in_locales() -> None:
    """
    No locale string may contain the two characters backslash + n.

    YAML single-quoted strings keep `\n` as a literal backslash-n, which renders
    as visible "\\n" text in Telegram notifications instead of a line break.
    Values that need newlines must use double-quoted YAML so the escape is
    processed. Regression: status.spam_guide_link / status.channel_link leaked
    literal "\\n" into the group-join promo message.
    """
    offenders = []
    for path in LOCALE_PATHS:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        strings = []
        _walk_strings(data, "", strings)
        for key, value in strings:
            if "\\n" in value:
                offenders.append(f"{path}: {key!r}")
    assert not offenders, (
        "Literal '\\n' in locale values (use double-quoted YAML for real "
        "newlines):\n  " + "\n  ".join(offenders)
    )
