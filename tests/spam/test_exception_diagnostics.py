"""#54: a message-less exception must still produce a diagnosable log line.

`str(exc)` is EMPTY for a whole class of failures — `TimeoutError`,
`asyncio.TimeoutError`, `asyncio.CancelledError`, `httpx.ReadTimeout`,
`httpx.ConnectError`, and any exception raised with no arguments. A log line built
from `{e}` therefore renders as `failed: ` with nothing after the colon, which is
indistinguishable from a defect in the logging itself.

Measured 2026-09-27: five classifier failures in one window all logged
`Gateway spam classification failed: ,` — establishing that they were timeouts took
timing analysis instead of a log read, because a dead retry layer, a provider
outage and a budget wall all render identically as nothing.
"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import patch

import pytest

# NOTE: `describe_exception` is imported INSIDE the tests that need it, never at module
# scope. A module-level import would make the pre-fix run fail at COLLECTION with an
# ImportError -- a degenerate failure that proves nothing about behaviour, and the exact
# shape that has twice made a guard look like it bites when it only failed to import.
# The path-level tests below import nothing new, so they fail on a REAL assertion.

# The classes whose str() is empty. `asyncio.TimeoutError` is an ALIAS of the builtin
# `TimeoutError` from 3.11 on, so it is asserted as an alias below rather than
# parametrized a second time — a second case would be a duplicate wearing a second name.
MESSAGE_LESS_EXCEPTIONS = [
    TimeoutError(),
    ValueError(),
    RuntimeError(),
]

# CancelledError is a BaseException, so `except Exception` does NOT catch it - it
# propagates out of is_spam, which is the correct cooperative-cancellation behaviour
# and is asserted separately below rather than folded into the logged-failure set.
MESSAGE_LESS_BASE_EXCEPTIONS = [
    asyncio.CancelledError(),
]


class _AlwaysFails:
    """An agent whose every call raises, standing in for a dead provider leg."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    async def run(self, *_args: object, **_kwargs: object) -> None:
        raise self._exc


@contextmanager
def _all_legs_failing(exc: BaseException) -> Iterator[None]:
    """Every classifier leg raises `exc`: the gateway, then the whole rotation pool."""
    from app.spam import spam_classifier

    failing = _AlwaysFails(exc)
    with (
        patch.object(spam_classifier, "build_system_prompt", return_value="sys"),
        patch.object(spam_classifier, "get_gateway_spam_agent", return_value=failing),
        patch.object(spam_classifier, "_get_openrouter_agents", return_value=[1, 2]),
        patch.object(spam_classifier, "_next_openrouter_agent", return_value=None),
        patch.object(spam_classifier, "get_openrouter_spam_agent", return_value=failing),
    ):
        yield


@pytest.mark.parametrize(
    "exc",
    MESSAGE_LESS_EXCEPTIONS + MESSAGE_LESS_BASE_EXCEPTIONS,
    ids=lambda e: type(e).__name__,
)
def test_describe_exception_names_the_type_when_the_message_is_empty(
    exc: BaseException,
) -> None:
    """The type is the token that is NEVER empty, so it must always be present."""
    from app.common.utils import describe_exception

    described = describe_exception(exc)
    assert described, f"empty description for {type(exc).__name__}"
    assert type(exc).__name__ in described, described


def test_describe_exception_appends_a_message_when_there_is_one() -> None:
    """A message-carrying exception keeps its message — the type is added, not swapped."""
    from app.common.utils import describe_exception

    described = describe_exception(ValueError("bad reply target"))
    assert described == "ValueError: bad reply target", described


@pytest.mark.parametrize(
    "exc", MESSAGE_LESS_EXCEPTIONS, ids=lambda e: type(e).__name__
)
async def test_classifier_failure_lines_name_the_type(
    exc: BaseException, caplog: pytest.LogCaptureFixture
) -> None:
    """The REAL path: every leg fails with a message-less exception.

    A guard on `describe_exception` alone would not prove the call sites USE it, so
    this drives `is_spam` itself and reads the lines it actually emits.
    """
    from app.spam import spam_classifier

    with (
        _all_legs_failing(exc),
        caplog.at_level(logging.WARNING),
        pytest.raises(RuntimeError),
    ):
        await spam_classifier.is_spam("buy cheap followers here")

    lines = [record.getMessage() for record in caplog.records]
    gateway = [line for line in lines if line.startswith("Gateway spam classification")]
    openrouter = [line for line in lines if line.startswith("OpenRouter agent")]
    assert gateway, f"the gateway leg logged nothing: {lines}"
    assert openrouter, f"no OpenRouter leg logged anything: {lines}"
    for line in gateway + openrouter:
        assert type(exc).__name__ in line, (
            f"a message-less failure rendered without its type, so the line is "
            f"indistinguishable from a logging defect: {line!r}"
        )


async def test_cancellation_is_not_swallowed_by_the_classifier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A cancelled classification must PROPAGATE, never be logged as a provider failure.

    `asyncio.CancelledError` is a BaseException, so `except Exception` does not catch
    it. That is deliberate: swallowing cancellation would turn a shutdown or a webhook
    timeout into a rotated provider pool, and the caller would never learn the task was
    cancelled. Guarded here because "names the type on every failure" must not become
    "catches every failure".
    """
    from app.spam import spam_classifier

    with (
        _all_legs_failing(asyncio.CancelledError()),
        caplog.at_level(logging.WARNING),
        pytest.raises(asyncio.CancelledError),
    ):
        await spam_classifier.is_spam("buy cheap followers here")


def test_asyncio_timeout_error_is_the_builtin_alias() -> None:
    """Both names reach the same class, so one guard covers both wire forms.

    Without this the set could silently stop covering the name the transport actually
    raises, which is how the original five failures came out blank.
    """
    assert asyncio.TimeoutError is TimeoutError
