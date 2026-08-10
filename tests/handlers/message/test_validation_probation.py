"""Tests for trusted-member early exit in message validation."""

import logging

import pytest
from unittest.mock import AsyncMock, patch

from src.app.handlers.message.validation import (
    fetch_linked_chat_id,
    get_and_check_group,
    validate_group_and_check_early_exits,
)


@pytest.mark.asyncio
async def test_probation_member_not_skipped():
    group_id = -100123
    user_id = 456
    mock_group = type(
        "Group",
        (),
        {"admin_ids": [999], "moderation_enabled": True},
    )()

    with (
        patch(
            "src.app.handlers.message.validation.get_and_check_group",
            new_callable=AsyncMock,
            return_value=(mock_group, ""),
        ),
        patch(
            "src.app.handlers.message.validation.is_trusted_member",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        group, reason = await validate_group_and_check_early_exits(group_id, user_id)
        assert group is mock_group
        assert reason == ""


@pytest.mark.asyncio
async def test_trusted_member_skipped():
    group_id = -100123
    user_id = 456
    mock_group = type(
        "Group",
        (),
        {"admin_ids": [999], "moderation_enabled": True},
    )()

    with (
        patch(
            "src.app.handlers.message.validation.get_and_check_group",
            new_callable=AsyncMock,
            return_value=(mock_group, ""),
        ),
        patch(
            "src.app.handlers.message.validation.is_trusted_member",
            new_callable=AsyncMock,
            return_value=True,
        ),
    ):
        _, reason = await validate_group_and_check_early_exits(group_id, user_id)
        assert reason == "message_trusted_member_skipped"


@pytest.mark.asyncio
async def test_fetch_linked_chat_id_logs_title_username_on_failure(caplog):
    """
    fetch_linked_chat_id failure log carries the passed title/username
    (no re-fetch — attrs come from the message.chat already in scope).
    """
    with patch("src.app.handlers.message.validation.bot") as mock_bot:
        mock_bot.get_chat = AsyncMock(side_effect=Exception("TelegramForbiddenError"))

        with caplog.at_level(logging.WARNING, logger="src.app.handlers.message.validation"):
            result = await fetch_linked_chat_id(
                -100123, "Test Group", "testgroup"
            )

        assert result is None

    assert any(
        r.levelname == "WARNING"
        and "Failed to fetch linked_chat_id" in r.getMessage()
        and "'Test Group' @testgroup" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_get_and_check_group_logs_title_username_on_missing(caplog):
    """
    get_and_check_group 'not found' log carries the passed title/username.
    """
    with patch("src.app.handlers.message.validation.get_group", new_callable=AsyncMock) as mock_get_group:
        mock_get_group.return_value = None

        with caplog.at_level(logging.INFO, logger="src.app.handlers.message.validation"):
            group, reason = await get_and_check_group(-100123, "Test Group", "testgroup")

        assert group is None
        assert reason == "error_message_group_not_found"

    assert any(
        r.levelname == "INFO"
        and "Group not found" in r.getMessage()
        and "'Test Group' @testgroup" in r.getMessage()
        for r in caplog.records
    )
