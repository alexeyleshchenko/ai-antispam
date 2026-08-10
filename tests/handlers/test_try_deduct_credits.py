import logging

import pytest
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat

from src.app.handlers.try_deduct_credits import try_deduct_credits


@pytest.mark.asyncio
async def test_try_deduct_credits_warning_includes_title_and_username(caplog):
    """
    Credit-failure WARNING renders the group title and username:
    'No paying admins in chat -100123 ('Test Group' @testgroup) for approve user'
    """
    chat = Chat(id=-100123, type="supergroup", title="Test Group", username="testgroup")

    with (
        patch(
            "src.app.handlers.try_deduct_credits.deduct_credits_from_admins",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("src.app.handlers.try_deduct_credits.bot") as mock_bot,
        patch(
            "src.app.handlers.try_deduct_credits.handle_deactivation",
            new_callable=AsyncMock,
        ),
    ):
        mock_bot.get_chat = AsyncMock(return_value=chat)

        with caplog.at_level(logging.WARNING, logger="src.app.handlers.try_deduct_credits"):
            result = await try_deduct_credits(-100123, 5, "approve user")

        assert result is False

    assert any(
        r.levelname == "WARNING"
        and "No paying admins in chat" in r.getMessage()
        and "'Test Group' @testgroup" in r.getMessage()
        and "approve user" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_try_deduct_credits_warning_falls_back_to_bare_id(caplog):
    """
    If get_chat fails, the WARNING still fires with the bare chat ID and
    does not crash (graceful fallback).
    """
    with (
        patch(
            "src.app.handlers.try_deduct_credits.deduct_credits_from_admins",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("src.app.handlers.try_deduct_credits.bot") as mock_bot,
        patch(
            "src.app.handlers.try_deduct_credits.handle_deactivation",
            new_callable=AsyncMock,
        ),
    ):
        mock_bot.get_chat = AsyncMock(side_effect=Exception("TelegramForbiddenError"))

        with caplog.at_level(logging.WARNING, logger="src.app.handlers.try_deduct_credits"):
            result = await try_deduct_credits(-100123, 5, "approve user")

        assert result is False

    assert any(
        r.levelname == "WARNING"
        and "No paying admins in chat" in r.getMessage()
        and "-100123" in r.getMessage()
        for r in caplog.records
    )
