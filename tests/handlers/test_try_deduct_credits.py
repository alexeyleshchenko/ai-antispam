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

        with caplog.at_level(
            logging.WARNING, logger="src.app.handlers.try_deduct_credits"
        ):
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

        with caplog.at_level(
            logging.WARNING, logger="src.app.handlers.try_deduct_credits"
        ):
            result = await try_deduct_credits(-100123, 5, "approve user")

        assert result is False

    assert any(
        r.levelname == "WARNING"
        and "No paying admins in chat" in r.getMessage()
        and "-100123" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_send_group_deactivation_message_logs_success_with_message_id(caplog):
    """
    Success path logs the outcome: chat context + message_id.
    (Regression: the 'silent success' bug — success used to log nothing.)
    """
    from types import SimpleNamespace

    from src.app.handlers.try_deduct_credits import send_group_deactivation_message

    chat = Chat(id=-100123, type="supergroup", title="Test Group", username="testgroup")
    min_admin = SimpleNamespace(user=SimpleNamespace(id=42))

    with (
        patch(
            "src.app.handlers.try_deduct_credits.get_admin",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(language_code="ru"),
        ),
        patch(
            "src.app.handlers.try_deduct_credits.get_add_to_group_url",
            return_value="https://t.me/ai_antispam",
        ),
        patch("src.app.handlers.try_deduct_credits.bot") as mock_bot,
    ):
        mock_bot.get_chat = AsyncMock(return_value=chat)
        mock_bot.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=12345)
        )

        with caplog.at_level(
            logging.INFO, logger="src.app.handlers.try_deduct_credits"
        ):
            await send_group_deactivation_message(
                -100123, "https://t.me/bot?start=42", min_admin, 0.0
            )

    assert any(
        r.levelname == "INFO"
        and "Deactivation message sent to" in r.getMessage()
        and "-100123 ('Test Group' @testgroup)" in r.getMessage()
        and "message_id=12345" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_send_group_deactivation_message_failure_logs_chat_context(caplog):
    """
    Failure path logs the outcome with chat context (chat_id + title + username),
    not a bare 'Failed to send group promo message' without chat context.
    """
    from types import SimpleNamespace

    from src.app.handlers.try_deduct_credits import send_group_deactivation_message

    chat = Chat(id=-100123, type="supergroup", title="Test Group", username="testgroup")
    min_admin = SimpleNamespace(user=SimpleNamespace(id=42))

    with (
        patch(
            "src.app.handlers.try_deduct_credits.get_admin",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(language_code="ru"),
        ),
        patch(
            "src.app.handlers.try_deduct_credits.get_add_to_group_url",
            return_value="https://t.me/ai_antispam",
        ),
        patch("src.app.handlers.try_deduct_credits.bot") as mock_bot,
    ):
        mock_bot.get_chat = AsyncMock(return_value=chat)
        mock_bot.send_message = AsyncMock(
            side_effect=Exception("TelegramForbiddenError: bot is not a member")
        )

        with caplog.at_level(
            logging.WARNING, logger="src.app.handlers.try_deduct_credits"
        ):
            await send_group_deactivation_message(
                -100123, "https://t.me/bot?start=42", min_admin, 0.0
            )

    assert any(
        r.levelname == "WARNING"
        and "Failed to send group deactivation message" in r.getMessage()
        and "-100123 ('Test Group' @testgroup)" in r.getMessage()
        and "bot is not a member" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_handle_deactivation_passes_metadata_to_set_group_moderation():
    """
    handle_deactivation writes the chat metadata it fetches (no-metadata gap):
    set_group_moderation is called with (chat_id, False, title, username).
    """
    from src.app.handlers.try_deduct_credits import handle_deactivation

    chat = Chat(id=-100123, type="supergroup", title="Test Group", username="testgroup")

    with (
        patch("src.app.handlers.try_deduct_credits.bot") as mock_bot,
        patch(
            "src.app.handlers.try_deduct_credits.set_group_moderation",
            new_callable=AsyncMock,
        ) as mock_set_moderation,
    ):
        mock_bot.get_chat = AsyncMock(return_value=chat)
        mock_bot.get_chat_administrators = AsyncMock(return_value=[])

        await handle_deactivation(-100123)

    mock_set_moderation.assert_awaited_once_with(
        -100123, False, "Test Group", "testgroup"
    )


@pytest.mark.asyncio
async def test_handle_deactivation_get_chat_failure_still_disables_moderation():
    """
    If get_chat fails, deactivation still happens (metadata best-effort):
    set_group_moderation called with None title/username, no crash.
    """
    from src.app.handlers.try_deduct_credits import handle_deactivation

    with (
        patch("src.app.handlers.try_deduct_credits.bot") as mock_bot,
        patch(
            "src.app.handlers.try_deduct_credits.set_group_moderation",
            new_callable=AsyncMock,
        ) as mock_set_moderation,
    ):
        mock_bot.get_chat = AsyncMock(side_effect=Exception("network down"))

        await handle_deactivation(-100123)

    mock_set_moderation.assert_awaited_once_with(-100123, False, None, None)
