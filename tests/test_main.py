import json
import logging
from unittest.mock import MagicMock

import pytest
from aiogram.exceptions import TelegramNetworkError

from src.app.main import (
    WEBHOOK_TIMEOUT,
    _update_type,
    handle_unhandled_exception,
    log_update_received,
)


@pytest.mark.asyncio
async def test_handle_unhandled_exception_returns_503_for_transient():
    span = MagicMock()
    err = TelegramNetworkError(method=MagicMock(), message="network down")
    response = await handle_unhandled_exception(
        span, err, {"update_id": 1}, elapsed=1.0
    )
    assert response.status == 503
    body = json.loads(response.text)
    assert body.get("retry") is True
    assert span.tags == ["webhook_retryable_error"]


@pytest.mark.asyncio
async def test_handle_unhandled_exception_acks_unknown():
    span = MagicMock()
    response = await handle_unhandled_exception(
        span, RuntimeError("bug"), {"update_id": 1}, elapsed=1.0
    )
    assert response.status == 200
    assert span.tags == ["unhandled_exception"]


@pytest.mark.asyncio
async def test_handle_unhandled_exception_no_retry_when_no_time_left():
    span = MagicMock()
    err = TelegramNetworkError(method=MagicMock(), message="network")
    elapsed = float(WEBHOOK_TIMEOUT - 1)
    response = await handle_unhandled_exception(
        span, err, {"update_id": 1}, elapsed=elapsed
    )
    assert response.status == 200


def test_main_imports():
    pass


# ─── update_id logging (#33) ─────────────────────────────────────────────────


def test_update_type_single():
    assert _update_type({"update_id": 1, "callback_query": {}}) == "callback_query"
    assert _update_type({"update_id": 1, "message": {}}) == "message"


def test_update_type_unknown_and_multiple():
    assert _update_type({"update_id": 1}) == "unknown"
    assert (
        _update_type({"update_id": 1, "message": {}, "edited_message": {}})
        == "multiple"
    )


def test_log_update_received_callback_logs_info_with_context(caplog):
    update = {
        "update_id": 726121960,
        "callback_query": {
            "data": "delete_spam_message:6075778132:-1001503592176:17406",
            "from": {"id": 7, "username": "alex"},
            "message": {"chat": {"id": -1001503592176}, "message_id": 17406},
        },
    }
    with caplog.at_level(logging.INFO):
        log_update_received(update)
    messages = [r.getMessage() for r in caplog.records]
    assert any("update_id=726121960" in m for m in messages)
    assert any("chat=-1001503592176" in m for m in messages)
    assert any("from=alex" in m for m in messages)
    assert any("delete_spam_message" in m for m in messages)


def test_log_update_received_non_callback_logs_debug(caplog):
    update = {"update_id": 43, "message": {"message_id": 1}}
    with caplog.at_level(logging.DEBUG):
        log_update_received(update)
    messages = [r.getMessage() for r in caplog.records]
    assert any("update_id=43" in m and "type=message" in m for m in messages)
