"""Tests for probation counter increments in the moderation pipeline."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.handlers.message.pipeline import (
    _maybe_increment_probation_events,
    handle_moderated_message,
    process_spam_or_approve,
)
from src.app.types import SpamClassificationContext
from tests.conftest import DEFAULT_SPAM_CONFIG


@pytest.mark.asyncio
async def test_maybe_increment_skips_on_first_approval(mock_message):
    with patch(
        "src.app.handlers.message.pipeline.increment_moderation_events",
        new_callable=AsyncMock,
    ) as mock_inc:
        await _maybe_increment_probation_events(
            mock_message.chat.id,
            123,
            was_approved_before=False,
            member_inserted_this_turn=True,
            result="message_user_approved",
        )
        mock_inc.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_increment_on_second_message(mock_message):
    with (
        patch(
            "src.app.handlers.message.pipeline.is_member_in_group",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.app.handlers.message.pipeline.increment_moderation_events",
            new_callable=AsyncMock,
        ) as mock_inc,
    ):
        await _maybe_increment_probation_events(
            mock_message.chat.id,
            123,
            was_approved_before=True,
            member_inserted_this_turn=False,
            result="message_user_approved",
        )
        mock_inc.assert_called_once()


@pytest.mark.asyncio
async def test_maybe_increment_skips_after_ban(mock_message):
    with (
        patch(
            "src.app.handlers.message.pipeline.is_member_in_group",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "src.app.handlers.message.pipeline.increment_moderation_events",
            new_callable=AsyncMock,
        ) as mock_inc,
    ):
        await _maybe_increment_probation_events(
            mock_message.chat.id,
            123,
            was_approved_before=True,
            member_inserted_this_turn=False,
            result="spam_admins_notified",
        )
        mock_inc.assert_not_called()


@pytest.mark.asyncio
async def test_process_spam_or_approve_returns_member_inserted(
    mock_message, mock_message_context_result
):
    with (
        patch(
            "src.app.handlers.message.pipeline.try_deduct_credits",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.app.handlers.message.pipeline.add_member",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.app.handlers.message.pipeline.load_config",
            return_value=DEFAULT_SPAM_CONFIG,
        ),
    ):
        result, inserted = await process_spam_or_approve(
            mock_message,
            False,
            95,
            [1],
            "ok",
            mock_message_context_result,
        )
        assert result == "message_user_approved"
        assert inserted is True


@pytest.mark.asyncio
async def test_handle_moderated_message_increments_after_probation_message(
    mock_message, mock_message_context_result
):
    mock_group = MagicMock()
    mock_group.admin_ids = [1]
    mock_group.moderation_enabled = True

    with (
        patch(
            "src.app.handlers.message.pipeline.is_member_in_group",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.app.handlers.message.pipeline.validate_group_and_check_early_exits",
            new_callable=AsyncMock,
            return_value=(mock_group, ""),
        ),
        patch(
            "src.app.handlers.message.pipeline.check_skip_channel_bot_message",
            new_callable=AsyncMock,
            return_value=(False, ""),
        ),
        patch(
            "src.app.handlers.message.pipeline.collect_message_context",
            new_callable=AsyncMock,
            return_value=mock_message_context_result,
        ),
        patch(
            "src.app.handlers.message.pipeline.classify_spam",
            new_callable=AsyncMock,
            return_value=(False, 95, "ham"),
        ),
        patch(
            "src.app.handlers.message.pipeline.save_message_lookup_entry",
            new_callable=AsyncMock,
        ),
        patch(
            "src.app.handlers.message.pipeline.process_spam_or_approve",
            new_callable=AsyncMock,
            return_value=("message_user_approved", False),
        ),
        patch(
            "src.app.handlers.message.pipeline.increment_moderation_events",
            new_callable=AsyncMock,
        ) as mock_inc,
        patch("src.app.handlers.message.pipeline.get_root_span") as mock_span,
    ):
        mock_span.return_value.set_attribute = MagicMock()
        result = await handle_moderated_message(mock_message)
        assert result == "message_user_approved"
        mock_inc.assert_called_once()


@pytest.mark.asyncio
async def test_handle_moderated_message_injects_chat_topic(
    mock_message, mock_message_context_result
):
    """group.topic_description_short reaches classify_spam as context.chat_topics.

    Regression for the wiring point between the stored topic profile and the
    classifier: the short label (not the full prose) is what gets injected —
    if the group shape or the ctx assignment changes, this test catches it
    (the injection is the only place chat_topics is set).
    """
    mock_group = MagicMock()
    mock_group.admin_ids = [1]
    mock_group.topic_description = (
        "A long full description that must NOT reach the classifier."
    )
    mock_group.topic_description_short = "PHP jobs"

    # Real context object — not a mock — so the attribute set is observable.
    ctx = SpamClassificationContext()
    mock_message_context_result.context = ctx

    with (
        patch(
            "src.app.handlers.message.pipeline.is_member_in_group",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "src.app.handlers.message.pipeline.validate_group_and_check_early_exits",
            new_callable=AsyncMock,
            return_value=(mock_group, ""),
        ),
        patch(
            "src.app.handlers.message.pipeline.check_skip_channel_bot_message",
            new_callable=AsyncMock,
            return_value=(False, ""),
        ),
        patch(
            "src.app.handlers.message.pipeline.collect_message_context",
            new_callable=AsyncMock,
            return_value=mock_message_context_result,
        ),
        patch(
            "src.app.handlers.message.pipeline.classify_spam",
            new_callable=AsyncMock,
            return_value=(False, 95, "ham"),
        ) as mock_classify,
        patch(
            "src.app.handlers.message.pipeline.save_message_lookup_entry",
            new_callable=AsyncMock,
        ),
        patch(
            "src.app.handlers.message.pipeline.process_spam_or_approve",
            new_callable=AsyncMock,
            return_value=("message_user_approved", False),
        ),
        patch(
            "src.app.handlers.message.pipeline.increment_moderation_events",
            new_callable=AsyncMock,
        ),
        patch("src.app.handlers.message.pipeline.get_root_span") as mock_span,
    ):
        mock_span.return_value.set_attribute = MagicMock()
        result = await handle_moderated_message(mock_message)

        assert result == "message_user_approved"
        # The injected topic must be visible on the context classify_spam got.
        classify_kwargs = mock_classify.call_args.kwargs
        assert classify_kwargs["context"].chat_topics == "PHP jobs"
