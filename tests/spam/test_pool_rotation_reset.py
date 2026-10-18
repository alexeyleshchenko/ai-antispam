"""#121: a success must return the pool to the slot the config declares.

The pool index used to advance on failure and never come back, so a single
transient error demoted the declared lead for the life of the process. Measured
in the running container: one ``TimeoutError`` at 2026-10-16T14:47:27Z and the
next 18 classifications (43.52 h) all started on slot 2, while
``config.yaml`` still declared slot 1 first.

Rotation is a per-message RECOVERY path, not a health ranking. The declared
order must be true at the start of every message; only failures encountered
while serving that message may move the index, and a success ends the recovery.

These tests drive the production ``is_spam`` loop against scripted agents, so
the state machine under test is the real one (``_openrouter_pool_idxs``), not a
re-implementation of it. No network, no model, no database.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import agents as agents_mod

SPAM_PREFIX = "openrouter-spam"

OK = SimpleNamespace(
    output=SimpleNamespace(is_spam=True, confidence=95, reason="тест"),
)

class _ScriptedAgent:
    """A pool slot that either serves a verdict or raises, on demand."""

    def __init__(self, name: str, outcome: object) -> None:
        self.name = name
        self.outcome = outcome
        self.calls = 0

    async def run(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch):
    """Install a two-slot pool and the classifier's non-LLM dependencies.

    The pool list and index are the REAL module state, so the production
    ``get_openrouter_spam_agent`` / ``_next_openrouter_agent`` /
    ``_reset_openrouter_agent`` path is exercised end to end.
    """
    from app.spam import spam_classifier

    lead = _ScriptedAgent("lead", TimeoutError())
    net = _ScriptedAgent("net", OK)

    monkeypatch.setitem(agents_mod._openrouter_pools, SPAM_PREFIX, [lead, net])
    monkeypatch.setitem(agents_mod._openrouter_pool_idxs, SPAM_PREFIX, 0)
    monkeypatch.setattr(
        spam_classifier, "build_system_prompt", AsyncMock(return_value="sys")
    )
    monkeypatch.setattr(spam_classifier, "get_llm_gateway_enabled", lambda: False)

    yield lead, net

    agents_mod._openrouter_pools.pop(SPAM_PREFIX, None)
    agents_mod._openrouter_pool_idxs.pop(SPAM_PREFIX, None)

async def test_a_success_returns_the_pool_to_the_declared_first_slot(pool) -> None:
    """The close condition of #121, driven through the production loop."""
    from app.spam import spam_classifier

    lead, net = pool

    # Message 1: the lead times out, the net serves. The index must be back at
    # the declared first slot once the message is decided.
    verdict = await spam_classifier.is_spam("первое сообщение")
    assert verdict[0] is True
    assert (lead.calls, net.calls) == (1, 1), "the net must have served the message"
    assert agents_mod._openrouter_pool_idxs[SPAM_PREFIX] == 0, (
        "the pool stayed on the slot a failure moved it to; the declared lead is "
        "demoted until the next failure happens to rotate back"
    )

    # Message 2: the lead is healthy again, so it must be tried FIRST.
    lead.outcome = OK
    await spam_classifier.is_spam("второе сообщение")
    assert lead.calls == 2, "the next classification did not start on the declared lead"
    assert net.calls == 1, "the net was tried again for a message the lead served"

async def test_a_failure_still_rotates_within_the_message(pool) -> None:
    """Reset-on-success must not remove the recovery rotation it follows."""
    from app.spam import spam_classifier

    lead, net = pool

    await spam_classifier.is_spam("сообщение")
    assert lead.calls == 1, "the lead was never tried"
    assert net.calls == 1, "a failed lead did not fall through to the net"
