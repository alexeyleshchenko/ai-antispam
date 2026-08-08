"""Message moderation pipeline: validation, spam analysis, result processing."""

import logging
from typing import Any

from aiogram import types

from ...common.trace_context import get_root_span
from ...common.utils import determine_effective_user_id, load_config
from ...database import (
    APPROVE_PRICE,
    DELETE_PRICE,
    add_member,
    increment_moderation_events,
    is_member_in_group,
    save_message_lookup_entry,
)
from ...spam.account_signals import build_account_signals_body
from ...spam.message_context import collect_message_context
from ...spam.spam_classifier import is_spam as classify_spam
from ...types import ContextStatus, MessageContextResult
from ..handle_spam import handle_spam
from ..try_deduct_credits import try_deduct_credits
from .validation import (
    check_skip_channel_bot_message,
    validate_group_and_check_early_exits,
)

logger = logging.getLogger(__name__)

# Results that completed moderation and may count toward probation (member still approved)
_PROBATION_INCREMENT_RESULTS: frozenset[str] = frozenset(
    {
        "message_user_approved",
        "message_low_confidence_review",
        "spam_admins_notified",
    }
)


def _context_to_lookup_strings(
    message_context_result: MessageContextResult,
) -> tuple[str | None, str | None]:
    """Extract stories and account_signals body for message_lookup_cache."""
    ctx = message_context_result.context
    stories_context = None
    if ctx.stories:
        if ctx.stories.status == ContextStatus.FOUND and ctx.stories.content:
            stories_context = ctx.stories.content
        elif ctx.stories.status == ContextStatus.EMPTY:
            stories_context = "[EMPTY]"
    account_signals_context = build_account_signals_body(ctx)
    return stories_context, account_signals_context


async def _maybe_increment_probation_events(
    chat_id: int,
    user_id: int,
    was_approved_before: bool,
    member_inserted_this_turn: bool,
    result: str,
) -> None:
    if member_inserted_this_turn or not was_approved_before:
        return
    if result not in _PROBATION_INCREMENT_RESULTS:
        return
    if not await is_member_in_group(chat_id, user_id):
        return
    await increment_moderation_events(chat_id, user_id)


async def _handle_trusted_member_exit(message: types.Message, user_id: int) -> None:
    """Save message lookup entry for a trusted member before early-exiting."""
    try:
        msg_text = message.text or message.caption or "[MEDIA_MESSAGE]"
        reply_text = None
        if message.reply_to_message:
            reply_text = (
                message.reply_to_message.text
                or message.reply_to_message.caption
                or "[MEDIA_MESSAGE]"
            )
        await save_message_lookup_entry(
            chat_id=message.chat.id,
            message_id=message.message_id,
            effective_user_id=user_id,
            message_text=msg_text,
            reply_to_text=reply_text,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to save message lookup for trusted user: {e}")


def _set_classification_span_attributes(
    target_span: Any,
    is_spam: bool,
    confidence: int,
    reason: str,
    message_context_result: MessageContextResult,
    source: str,
) -> None:
    """Set Logfire span attributes from classification results."""
    target_span.set_attribute("llm_is_spam", is_spam)
    target_span.set_attribute("llm_confidence", confidence)
    target_span.set_attribute("llm_reason", reason)
    target_span.set_attribute("context", str(message_context_result.context))
    target_span.set_attribute("moderation_source", source)


async def _save_classification_lookup(
    message: types.Message,
    message_context_result: MessageContextResult,
    user_id: int,
) -> None:
    """Save message lookup entry with context after spam classification."""
    try:
        stories_ctx, account_ctx = _context_to_lookup_strings(message_context_result)
        reply_ctx = message_context_result.context.reply
        msg_text = message.text or message.caption or "[MEDIA_MESSAGE]"
        await save_message_lookup_entry(
            chat_id=message.chat.id,
            message_id=message.message_id,
            effective_user_id=user_id,
            message_text=msg_text,
            reply_to_text=reply_ctx,
            stories_context=stories_ctx,
            account_signals_context=account_ctx,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to save message lookup after classification: {e}")


async def handle_moderated_message(
    message: types.Message, *, source: str = "new"
) -> str:
    """
    Process message through spam pipeline: validate, classify, act.

    Returns result identifier for logging (e.g. message_user_approved, spam_auto_deleted).
    """
    user_id = determine_effective_user_id(message)
    if user_id is None:
        return "message_no_user_info"

    chat_id = message.chat.id
    was_approved_before = await is_member_in_group(chat_id, user_id)

    group, exit_reason = await validate_group_and_check_early_exits(chat_id, user_id)
    if exit_reason or group is None:
        if exit_reason == "message_trusted_member_skipped":
            await _handle_trusted_member_exit(message, user_id)
        return exit_reason

    logger.debug(
        f"sender_chat={getattr(message, 'sender_chat', None)}, "
        f"chat.linked_chat_id={getattr(message.chat, 'linked_chat_id', None)}"
    )
    skip, reason = await check_skip_channel_bot_message(message)
    if skip:
        return reason

    message_context_result = await collect_message_context(message)

    try:
        if message_context_result.is_story:
            is_spam, confidence, reason = True, 100, "Story forward"
        else:
            is_spam, confidence, reason = await classify_spam(
                comment=message_context_result.message_text,
                admin_ids=group.admin_ids,
                context=message_context_result.context,
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to get spam classification: {e}")
        return "message_spam_check_failed"

    target_span = get_root_span()
    _set_classification_span_attributes(
        target_span,
        is_spam,
        confidence,
        reason,
        message_context_result,
        source,
    )

    await _save_classification_lookup(message, message_context_result, user_id)

    result, member_inserted = await process_spam_or_approve(
        message,
        is_spam,
        confidence,
        group.admin_ids,
        reason,
        message_context_result,
    )

    await _maybe_increment_probation_events(
        chat_id, user_id, was_approved_before, member_inserted, result
    )
    return result


async def process_spam_or_approve(
    message: types.Message,
    is_spam: bool,
    confidence: int,
    admin_ids: list[int],
    reason: str,
    message_context_result: MessageContextResult,
) -> tuple[str, bool]:
    """Apply spam result: delete/notify or approve user. Returns (result_id, member_inserted)."""
    chat_id = message.chat.id
    user_id = determine_effective_user_id(message)
    if user_id is None:
        return "message_no_user_info", False

    threshold = load_config().get("spam", {}).get("high_confidence_threshold", 90)
    member_inserted = False

    if is_spam:
        skip_auto_delete = confidence < threshold
        if await try_deduct_credits(chat_id, DELETE_PRICE, "delete spam"):
            result = await handle_spam(
                message,
                admin_ids,
                reason,
                message_context_result,
                skip_auto_delete=skip_auto_delete,
            )
            return result, False

    elif confidence < threshold:
        if await try_deduct_credits(chat_id, APPROVE_PRICE, "approve user"):
            member_inserted = await add_member(chat_id, user_id)
            result = await handle_spam(
                message,
                admin_ids,
                reason,
                message_context_result,
                skip_auto_delete=True,
                is_low_confidence_not_spam=True,
                confidence=confidence,
            )
            return "message_low_confidence_review", member_inserted

    elif await try_deduct_credits(chat_id, APPROVE_PRICE, "approve user"):
        member_inserted = await add_member(chat_id, user_id)
        return "message_user_approved", member_inserted

    return "message_insufficient_credits", False
