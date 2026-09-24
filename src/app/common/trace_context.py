"""
Trace context utilities for storing and retrieving the root span.

OpenTelemetry does not provide an API to walk from the current span to the root.
We store the root span in the trace context when it is created (in main.py)
and retrieve it downstream via get_root_span(). The context propagates through
async/await automatically.
"""

import contextvars
import time
from typing import Any, Protocol, cast

from opentelemetry import context
from opentelemetry.trace import get_current_span
from opentelemetry.util.types import AttributeValue

ROOT_SPAN_KEY = context.create_key("logfire_root_span")


class SpanLike(Protocol):
    def set_attribute(self, key: str, value: AttributeValue) -> None: ...


def set_root_span(span: Any) -> None:
    """
    Store the root span in the current context so it can be retrieved downstream.
    Call this when entering the root span (e.g., in the update handler).
    """
    ctx = context.get_current()
    ctx = context.set_value(ROOT_SPAN_KEY, span, ctx)
    context.attach(ctx)


def get_root_span() -> SpanLike:
    """
    Get the root span from the current context, if one was stored via set_root_span().
    Falls back to the current span when the root span is not set.
    """
    span = context.get_value(ROOT_SPAN_KEY)
    if span is not None and hasattr(span, "set_attribute"):
        return cast("SpanLike", span)
    return cast("SpanLike", get_current_span())


# Request deadline for the webhook turn. A plain ContextVar (not the
# OpenTelemetry attach used by set_root_span) because asyncio.create_task copies
# contextvars, so a detached task inherits the deadline it was created under.
# Holds a time.monotonic() deadline, never wall-clock: monotonic cannot go
# backwards when the host clock steps.
_WEBHOOK_DEADLINE_KEY: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "webhook_deadline", default=None
)


def set_webhook_deadline(seconds_from_now: float) -> None:
    """Stamp the deadline for the current request.

    Called once per update in main.py. The value is the number of seconds this
    request may consume, counted from now.
    """
    _WEBHOOK_DEADLINE_KEY.set(time.monotonic() + seconds_from_now)


def remaining_webhook_seconds() -> float | None:
    """Seconds left before this request's deadline, or None when no deadline is set.

    None means "no request context" (tests, direct calls, background jobs) - the
    caller falls back to the configured budget. A negative value is real and is
    returned as such: the caller has already run out of time and must not start
    new work.
    """
    deadline = _WEBHOOK_DEADLINE_KEY.get()
    if deadline is None:
        return None
    return deadline - time.monotonic()
