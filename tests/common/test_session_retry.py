"""Tests for session-level retry middleware (bot.py:setup_session_retry).

Test 1e — retries on transport error.
Test 1f — does NOT retry on bad request.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage

def _make_bot():
    bot = Bot(token="123:abc", session=AsyncMock())
    # Replace the mock middleware manager with a real one so the
    # @bot.session.middleware() decorator actually registers functions.
    from aiogram.client.session.middlewares.manager import RequestMiddlewareManager

    bot.session.middleware = RequestMiddlewareManager()
    return bot


# ---------------------------------------------------------------------------
# Test 1e — retries on transport error
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_middleware_retries_on_network_error():
    """Session middleware retries on TelegramNetworkError, then succeeds."""
    bot = _make_bot()
    from src.app.common.bot import setup_session_retry

    setup_session_retry(bot)

    # Get the registered middleware function from the manager
    middlewares = bot.session.middleware._middlewares
    assert middlewares, "No middleware registered — did setup_session_retry run?"
    middleware_fn = middlewares[0]

    make_request = AsyncMock()
    make_request.side_effect = [
        TelegramNetworkError(method=MagicMock(), message="network down"),
        TelegramNetworkError(method=MagicMock(), message="still down"),
        MagicMock(),  # success on 3rd attempt
    ]
    method = SendMessage(chat_id=123, text="hi")

    result = await middleware_fn(make_request, bot, method)

    assert make_request.call_count == 3
    assert result is not None  # final response returned


# ---------------------------------------------------------------------------
# Test 1f — does NOT retry on bad request
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_retry_middleware_does_not_retry_on_bad_request():
    """Session middleware does NOT retry TelegramBadRequest."""
    bot = _make_bot()
    from src.app.common.bot import setup_session_retry

    setup_session_retry(bot)

    middlewares = bot.session.middleware._middlewares
    assert middlewares
    middleware_fn = middlewares[0]

    make_request = AsyncMock()
    err = TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
    make_request.side_effect = err
    method = SendMessage(chat_id=999, text="hi")

    with pytest.raises(TelegramBadRequest):
        await middleware_fn(make_request, bot, method)

    assert make_request.call_count == 1