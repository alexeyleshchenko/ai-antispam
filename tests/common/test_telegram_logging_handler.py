import asyncio
import contextlib
import logging

import pytest

from app.common.telegram_logging_handler import TelegramLogHandler


class DummyBot:
    def __init__(self):
        self.calls = []

    async def send_message(
        self,
        chat_id,
        text,
        parse_mode=None,
        disable_web_page_preview=None,
    ):
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_web_page_preview,
            }
        )


@pytest.mark.asyncio
async def test_handler_flushes_after_loop_registration():
    bot = DummyBot()
    handler = TelegramLogHandler(
        bot=bot,  # type: ignore
        chat_id=123,
        throttling_capacity=5,
        dedupe_window=0.5,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    logger = logging.getLogger("tests.telegram_logger.flush")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False

    logger.warning("First warning")
    assert bot.calls == []

    handler.set_event_loop(asyncio.get_running_loop())

    await asyncio.sleep(0.5)  # Give time for background task to process

    logger.removeHandler(handler)

    assert len(bot.calls) == 1
    payload = bot.calls[0]
    assert payload["chat_id"] == 123
    assert payload["parse_mode"] == "HTML"
    assert "First warning" in payload["text"]


@pytest.mark.asyncio
async def test_handler_drains_queue_on_stop():
    """stop() drains any remaining queued messages, even if the task is still running."""
    bot = DummyBot()
    handler = TelegramLogHandler(
        bot=bot,  # type: ignore
        chat_id=123,
        throttling_capacity=999,  # no throttle limit
        dedupe_window=0.0,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    loop = asyncio.get_running_loop()
    handler.set_event_loop(loop)

    logger = logging.getLogger("tests.telegram_logger.drain")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False

    # Queue a message (will be processed by the background task)
    logger.warning("queued before stop")
    await asyncio.sleep(0.3)

    # While task is still running, call stop() - it should drain
    await handler.stop(timeout=5.0)

    # Exactly 1 call (the one processed before stop) — drain works
    assert len(bot.calls) == 1
    assert "queued before stop" in bot.calls[0]["text"]


@pytest.mark.asyncio
async def test_handler_stop_drains_remaining_on_timeout():
    """If stop() times out waiting for the task, it still drains the queue."""
    bot = DummyBot()
    handler = TelegramLogHandler(
        bot=bot,  # type: ignore
        chat_id=123,
        throttling_capacity=999,
        dedupe_window=0.0,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.set_event_loop(asyncio.get_running_loop())

    logger = logging.getLogger("tests.telegram_logger.timeout_drain")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False

    # Queue 3 messages
    logger.warning("msg1")
    logger.warning("msg2")
    logger.warning("msg3")

    # stop() with a very short timeout — task won't finish in time
    await handler.stop(timeout=0.01)

    # All 3 messages should be drained despite timeout
    assert len(bot.calls) == 3
    assert bot.calls[0]["text"] == "<pre>msg1</pre>"
    assert bot.calls[1]["text"] == "<pre>msg2</pre>"
    assert bot.calls[2]["text"] == "<pre>msg3</pre>"


class FailingBot:
    """Bot whose first `fail_times` sends raise, then succeed."""

    def __init__(self, fail_times: int = 1, error: BaseException | None = None):
        self.calls: list[dict] = []
        self.attempts = 0
        self.fail_times = fail_times
        self.error = error

    async def send_message(
        self,
        chat_id,
        text,
        parse_mode=None,
        disable_web_page_preview=None,
    ):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise (
                self.error
                if self.error is not None
                else RuntimeError("simulated send failure")
            )
        self.calls.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_web_page_preview,
            }
        )


def _attach(handler, name):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


@pytest.mark.asyncio
async def test_send_failure_does_not_kill_the_loop():
    """
    One failed send must not end log forwarding (#66).

    Regression: `_process_queue` awaited `_send` unguarded, so a single
    TelegramAPIError ended the task permanently and every later error went
    unreported - "no notifications" became indistinguishable from "no errors".
    """
    bot = FailingBot(fail_times=1)
    handler = TelegramLogHandler(
        bot=bot,  # type: ignore
        chat_id=123,
        throttling_capacity=999,
        dedupe_window=0.0,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._retry_base_delay = 0.01
    logger = _attach(handler, "tests.telegram_logger.survives")

    try:
        handler.set_event_loop(asyncio.get_running_loop())
        logger.warning("first after a failed send")
        await asyncio.sleep(0.4)
        logger.warning("second, proves the loop is alive")
        await asyncio.sleep(0.4)
    finally:
        logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            await handler.stop()

    texts = [c["text"] for c in bot.calls]
    assert any("first after a failed send" in t for t in texts), (
        f"the message was never retried past one failed send; calls={texts}"
    )
    assert any("second, proves the loop is alive" in t for t in texts), (
        "the send loop died after one failure - every later error is never forwarded"
    )


@pytest.mark.asyncio
async def test_permanently_failing_send_is_reported_not_swallowed(caplog):
    """A send that can never succeed is reported, not swallowed silently (#66)."""
    bot = FailingBot(fail_times=10_000)
    handler = TelegramLogHandler(
        bot=bot,  # type: ignore
        chat_id=123,
        throttling_capacity=999,
        dedupe_window=0.0,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._retry_base_delay = 0.01
    logger = _attach(handler, "tests.telegram_logger.dropped")

    try:
        handler.set_event_loop(asyncio.get_running_loop())
        with caplog.at_level(
            logging.ERROR, logger="app.common.telegram_logging_handler"
        ):
            logger.critical("this one cannot be delivered")
            await asyncio.sleep(0.5)
    finally:
        logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            await handler.stop()

    dropped = [r for r in caplog.records if "dropped a log message" in r.getMessage()]
    assert dropped, (
        "a permanently failing send was swallowed: no ERROR survived to say the "
        "message was lost"
    )
    assert dropped[0].levelno >= logging.ERROR
    assert "RuntimeError" in dropped[0].getMessage(), (
        "the report must name the exception type, or the next occurrence of this "
        f"class is equally invisible; got {dropped[0].getMessage()!r}"
    )


@pytest.mark.asyncio
async def test_send_task_restarts_after_unexpected_death(monkeypatch):
    """
    A send task that dies anyway is restarted, not left dead (#66).

    Silent death here is worse than the error it failed to deliver.
    """
    bot = DummyBot()
    handler = TelegramLogHandler(
        bot=bot,  # type: ignore
        chat_id=123,
        throttling_capacity=999,
        dedupe_window=0.0,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler._restart_delay = 0.01
    handler._retry_base_delay = 0.01

    real_process_queue = handler._process_queue
    state = {"calls": 0}

    async def dying_once():
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("simulated unexpected death")
        await real_process_queue()

    monkeypatch.setattr(handler, "_process_queue", dying_once)
    logger = _attach(handler, "tests.telegram_logger.supervisor")

    try:
        handler.set_event_loop(asyncio.get_running_loop())
        await asyncio.sleep(0.2)
        logger.warning("delivered by the restarted task")
        await asyncio.sleep(0.5)
    finally:
        logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            await handler.stop()

    texts = [c["text"] for c in bot.calls]
    assert state["calls"] >= 2, (
        "the send task was not restarted after it died - log forwarding stays dead "
        "and the owner hears nothing"
    )
    assert any("delivered by the restarted task" in t for t in texts), (
        f"no message was delivered after the send task died; calls={texts}"
    )
