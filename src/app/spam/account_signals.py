"""Profile photo age + Telegram Premium bundled as account signals for LLM and persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import ContextResult, ContextStatus

if TYPE_CHECKING:
    from ..types import SpamClassificationContext

ACCOUNT_SIGNALS_HEADER = "ACCOUNT SIGNALS"


def _profile_photo_line(result: ContextResult | None) -> str | None:
    """Single-line body for profile photo age ContextResult; None if SKIPPED or absent."""
    if not result or result.status == ContextStatus.SKIPPED:
        return None
    if result.status == ContextStatus.EMPTY:
        return "photo_age=unknown"
    if result.status == ContextStatus.FAILED:
        err = result.error or "unknown"
        return f"verification failed: {err}"
    if result.status == ContextStatus.FOUND and result.content is not None:
        if hasattr(result.content, "to_prompt_fragment"):
            return str(result.content.to_prompt_fragment())
        return str(result.content)
    return None


def build_account_signals_body(context: SpamClassificationContext) -> str | None:
    """
    Lines under ACCOUNT SIGNALS (no header). Used for DB/cache and LLM section body.
    """
    lines: list[str] = []
    if pl := _profile_photo_line(context.profile_photo_age):
        lines.append(pl)
    if not context.is_channel_sender and context.is_premium is not None:
        lines.append(f"is_premium={str(context.is_premium).lower()}")
    return "\n".join(lines) if lines else None


def format_account_signals_user_section(context: SpamClassificationContext) -> str:
    """Full ACCOUNT SIGNALS block for the LLM user message, or empty string."""
    if context.account_signals_snapshot is not None:
        snap = context.account_signals_snapshot.strip()
        return f"{ACCOUNT_SIGNALS_HEADER}:\n{snap}\n" if snap else ""
    body = build_account_signals_body(context)
    return f"{ACCOUNT_SIGNALS_HEADER}:\n{body}\n" if body else ""


def context_includes_account_signals(context: SpamClassificationContext) -> bool:
    """True when format_account_signals_user_section would be non-empty."""
    if context.account_signals_snapshot is not None:
        return bool(context.account_signals_snapshot.strip())
    return build_account_signals_body(context) is not None
