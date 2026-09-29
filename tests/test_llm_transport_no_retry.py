"""Guard: the transport retry layer on both LLM legs must be explicit and inert.

WHY THIS EXISTS (issue #50)
---------------------------
The client factory in `app/agents.py` (now `_create_llm_client`) configured tenacity with

    retry=lambda e: isinstance(e, (httpx.HTTPStatusError, httpx.ConnectError))

but tenacity hands that callable a **RetryCallState**, never an exception
(installed tenacity `__init__.py`: `self.retry(retry_state)`). So the predicate is
always False and the policy has never fired — the `wait` and `stop` strategies
are dead code that can never spend the leg.

Worse, the "obvious" way to delete the layer is a trap: omitting the `retry` key
does NOT disable retrying. tenacity's defaults are
`retry_if_exception_type(Exception)` (retry everything) with `stop_never`, so an
omitted key composes into an UNBOUNDED retry loop. Measured: an empty
`RetryConfig()` never terminates.

So the fixed shape is explicit: `retry=retry_never`, no `wait`, no `stop`.
`validate_response` stays wired, so a 429/5xx still raises and the caller's own
pool advances on it (spam_classifier.py iterates the OpenRouter pool).

WHAT THIS ASSERTS
-----------------
The property that matters is *"an HTTPStatusError must not be retried"* — not the
absence of particular keys. A purely behavioural transport-level test cannot
discriminate shipped from fixed code (both make exactly one request, because the
shipped predicate is dead), so the discriminating legs are the config shape plus
a direct call of the retry predicate with a real exception.

Legs (a)-(c) fail on shipped code; (d) is the end-to-end wiring check that must
pass on both revisions.
"""

import httpx
import pytest
from tenacity import RetryCallState

from app.agents import _create_gateway_model, _create_openrouter_model

# Provider env is absent in CI; the factories read these module globals at call
# time, so they are patched rather than requiring a .env in the runner.
_FAKE_KEY = "test-key-not-a-secret"


@pytest.fixture
def model_factories(monkeypatch):
    """Yield both model factories with provider env patched in."""
    from app import agents

    monkeypatch.setattr(agents, "FALLBACK_API_KEY", _FAKE_KEY, raising=False)
    monkeypatch.setattr(
        agents, "GATEWAY_API_BASE", "https://gw.invalid/v1", raising=False
    )
    monkeypatch.setattr(agents, "GATEWAY_API_KEY", _FAKE_KEY, raising=False)
    monkeypatch.setattr(agents, "GATEWAY_MODEL", "test-gateway-model", raising=False)
    return (
        ("gateway", lambda: _create_gateway_model()),
        ("openrouter", lambda: _create_openrouter_model("test/model:free")),
    )


def _status_error() -> httpx.HTTPStatusError:
    """A genuine HTTPStatusError (429) with request and response attached."""
    req = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    resp = httpx.Response(429, request=req)
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return exc
    raise AssertionError("unreachable: 429 always raises")


def _predicate_retries(fn, exc) -> bool:
    """True iff the retry predicate would retry `exc`.

    Two-call form, both needed:
      * the BARE call discriminates the shipped dead lambda (it receives an
        exception and answers True) — a RetryCallState alone cannot, because the
        dead lambda answers False for that too;
      * the RetryCallState fallback serves strategy objects such as
        `retry_if_exception_type(...)`, which dereference `retry_state.outcome`.

    The state is built EXPLICITLY. `set_exception` takes an (type, exc, tb) tuple;
    passing `sys.exc_info()` from outside an except block yields (None, None, None),
    which leaves `outcome.failed` False and makes a WORKING retry read as inert —
    a silent inversion of this very guard.
    """
    try:
        return bool(fn(exc))
    except AttributeError, TypeError:
        pass
    state = RetryCallState(retry_object=None, fn=None, args=(), kwargs={})
    state.set_exception((type(exc), exc, None))
    return bool(fn(state))


@pytest.mark.asyncio
@pytest.mark.parametrize("leg", ["gateway", "openrouter"])
async def test_transport_retry_layer_is_explicit_and_inert(leg, model_factories):
    """The retry layer must be inert, and must be *explicitly* inert."""
    factories = dict(model_factories)
    model = factories[leg]()
    transport = model.provider.client._client._transport
    config = transport.config

    # (a) no wait strategy — a wait can sleep away the entire per-attempt leg.
    assert config.get("wait") is None, (
        f"[{leg}] wait strategy must be absent; got {config.get('wait')!r} "
        f"({type(config.get('wait')).__name__}). A wait can sleep away the whole "
        f"per-attempt budget before the request it waited to send."
    )

    # (b) no stop strategy — an attempt budget implies retries happen at all.
    assert config.get("stop") is None, (
        f"[{leg}] stop strategy must be absent; got {config.get('stop')!r} "
        f"({type(config.get('stop')).__name__})."
    )

    # (c) the trap lock: the key must be PRESENT, because an omitted `retry`
    #     composes tenacity's defaults (retry everything + stop_never) = hang.
    assert "retry" in config and config["retry"] is not None, (
        f"[{leg}] retry strategy must be set explicitly; an omitted key composes "
        f"tenacity's default retry_if_exception_type(Exception) with stop_never, "
        f"which is an unbounded retry loop."
    )

    # (d) the property itself: an HTTPStatusError must NOT be retried.
    exc = _status_error()
    assert not _predicate_retries(config["retry"], exc), (
        f"[{leg}] the retry predicate accepts HTTPStatusError ({exc!r}); the pool "
        f"advances on failure, so a second invisible retry layer must not exist."
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("leg", ["gateway", "openrouter"])
async def test_validate_response_still_raises_after_one_request(leg, model_factories):
    """A 429 must still raise, on exactly one request to the wrapped transport.

    This is the wiring leg: removing the retry must not remove the raise, because
    the caller's pool depends on the exception to advance to the next model.
    """
    factories = dict(model_factories)
    model = factories[leg]()
    transport = model.provider.client._client._transport

    assert transport.validate_response is not None, (
        f"[{leg}] validate_response must stay wired, or a 429/5xx never raises and "
        f"the caller's pool cannot advance."
    )

    class _Always429(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self.count = 0

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.count += 1
            return httpx.Response(429, request=request)

    mock = _Always429()
    transport.wrapped = mock

    with pytest.raises(httpx.HTTPStatusError):
        await transport.handle_async_request(
            httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        )

    assert mock.count == 1, (
        f"[{leg}] expected exactly one request to the wrapped transport; got "
        f"{mock.count} — the retry layer must not re-issue the request."
    )
