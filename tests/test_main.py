import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramNetworkError

from src.app.main import (
    WEBHOOK_TIMEOUT,
    _update_type,
    handle_classification_pending,
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

# ─── detached update feed: the guard cancels the WAIT, not the work ──────────


@pytest.mark.asyncio
async def test_classification_pending_returns_503_with_retry():
    """A pending verdict asks for a redelivery, in the same shape as a timeout."""
    span = MagicMock()
    response = await handle_classification_pending(span, {"update_id": 1}, elapsed=2.5)
    assert response.status == 503
    body = json.loads(response.text)
    assert body.get("retry") is True
    assert body.get("error") == "Classification pending"
    assert span.tags == ["classification_pending"]


def _fake_request(payload: dict) -> MagicMock:
    request = MagicMock()
    request.read = AsyncMock(return_value=b"{}")
    request.json = AsyncMock(return_value=payload)
    return request


@pytest.mark.asyncio
async def test_handle_update_503_while_the_task_still_runs(monkeypatch):
    """The update outlives the guard: 503 now, and the work is NOT cancelled.

    This is the whole point of detaching. Under a bare wait_for the coroutine
    is cancelled at the deadline and its verdict is lost; shielded, the task
    keeps running and completes after the response has gone out.
    """
    from src.app import main

    finished = asyncio.Event()

    async def _slow_feed(bot, json):
        await asyncio.sleep(0.4)
        finished.set()
        return "message_ignored"

    monkeypatch.setattr(main, "WEBHOOK_TIMEOUT", 0.1)
    monkeypatch.setattr(main.dp, "feed_raw_update", _slow_feed)

    payload = {
        "update_id": 1,
        "message": {"message_id": 5, "chat": {"id": -100123, "title": "t"}},
    }

    response = await main.handle_update(_fake_request(payload))

    assert response.status == 503
    assert json.loads(response.text)["retry"] is True
    assert not finished.is_set(), "the work must still be running when 503 goes out"

    # It was detached, not cancelled: it completes on its own afterwards.
    (task,) = main._update_tasks
    await asyncio.wait_for(task, timeout=3)
    assert finished.is_set()
    assert task.cancelled() is False, "the guard must not have cancelled the work"
    # The done callback is scheduled via call_soon, so give the loop a turn.
    await asyncio.sleep(0)
    assert not main._update_tasks, "the finished task must be discarded"


@pytest.mark.asyncio
async def test_handle_update_returns_200_when_the_handler_is_fast(monkeypatch):
    """The ordinary path is unchanged: a fast handler still answers 200."""
    from src.app import main

    async def _fast_feed(bot, json):
        return "message_user_approved"

    monkeypatch.setattr(main, "WEBHOOK_TIMEOUT", 5.0)
    monkeypatch.setattr(main.dp, "feed_raw_update", _fast_feed)

    payload = {
        "update_id": 2,
        "message": {"message_id": 6, "chat": {"id": -100123, "title": "t"}},
    }

    response = await main.handle_update(_fake_request(payload))

    assert response.status == 200
    assert json.loads(response.text)["message"] == "Processed successfully"
