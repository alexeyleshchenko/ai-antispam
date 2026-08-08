"""
Internationalization for bot messages.

Supports ru and en. Unknown/unsupported languages normalize to "en".
"""

import logging
from pathlib import Path
from typing import Any

import yaml
from aiogram import types

logger = logging.getLogger(__name__)

_SUPPORTED = {"ru", "en"}
_DEFAULT_LANG = "en"
_LOCALES: dict[str, dict[str, Any]] = {}

# Inline help-section callback_data — same strings as t() keys (≤64 bytes for Telegram).
HELP_PAGE_CALLBACK_KEYS: frozenset[str] = frozenset(
    {
        "help.getting_started",
        "help.training",
        "help.moderation",
        "help.commands",
        "help.payment",
        "help.support",
    }
)


def _get_locales_dir() -> Path:
    """Return path to locales directory."""
    return Path(__file__).parent / "locales"


def load_locales() -> None:
    """Load ru.yaml and en.yaml from locales directory."""
    locales_dir = _get_locales_dir()
    for lang in _SUPPORTED:
        path = locales_dir / f"{lang}.yaml"
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _LOCALES[lang] = yaml.safe_load(f) or {}
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
                logger.warning(f"Failed to load locale {lang}: {e}")
                _LOCALES[lang] = {}
        else:
            _LOCALES[lang] = {}


def _get_nested(data: dict, key: str) -> Any:
    """Get value by dot-separated key, e.g. 'help.main'."""
    for part in key.split("."):
        if isinstance(data, dict) and part in data:
            data = data[part]
        else:
            return None
    return data


def normalize_lang(code: str | None) -> str:
    """
    Normalize language code to supported value.
    en-US -> en, ru -> ru, unknown/unsupported -> en.
    """
    if not code:
        return _DEFAULT_LANG
    base = code.split("-")[0].lower()
    return base if base in _SUPPORTED else _DEFAULT_LANG


def t(lang: str, key: str, **kwargs: Any) -> str:
    """
    Get localized string by key. Supports dot notation (e.g. help.main).
    Fallback: if key missing in requested lang, try the other supported lang.
    kwargs are used for .format() substitution.
    """
    if not _LOCALES:
        load_locales()

    normalized = normalize_lang(lang)
    langs_to_try = [normalized]
    if normalized == "en":
        langs_to_try.append("ru")
    else:
        langs_to_try.append("en")

    for lang_code in langs_to_try:
        data = _LOCALES.get(lang_code, {})
        val = _get_nested(data, key)
        if val is not None and isinstance(val, str):
            try:
                return val.format(**kwargs) if kwargs else val
            except KeyError:
                logger.warning(
                    f"Missing format placeholder in {key} for lang {lang_code}"
                )
                return val
    logger.warning(f"Missing translation key: {key}")
    return key


def resolve_lang(
    message_or_user: types.Message | types.User | None = None,
    admin: Any = None,
) -> str:
    """
    Resolve display language. Priority:
    1. admin.language_code (if admin and has language_code)
    2. message.from_user.language_code (or user.language_code)
    3. "en"
    """
    if admin is not None and hasattr(admin, "language_code") and admin.language_code:
        return normalize_lang(admin.language_code)

    user = None
    if isinstance(message_or_user, types.Message) and message_or_user.from_user:
        user = message_or_user.from_user
    elif isinstance(message_or_user, types.User):
        user = message_or_user

    if user and getattr(user, "language_code", None):
        return normalize_lang(user.language_code)

    return _DEFAULT_LANG
