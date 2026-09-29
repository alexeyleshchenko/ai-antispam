"""Regression guard: the classifier fallback leg's PROVIDER must be configurable.

Measured 2026-09-29. The fallback tier was pinned to a hardcoded OpenRouter
endpoint, and OpenRouter went to 0% availability for us:

  * free models -> 429, ``free-models-per-day`` cap (limit 1000 / remaining 0);
  * every paid model -> 404, ``Model blocked by guardrail`` (10 probed, all blocked).

Because the endpoint was baked into the source, the leg could not be moved without
a code change, so each gateway timeout became a lost message: 14 in 24 h (5.9%).

The property is two-sided and both sides bind:

  * set -> the leg must use ``FALLBACK_API_BASE`` / ``FALLBACK_API_KEY``;
  * unset -> it must still resolve to the OpenRouter defaults, so a deployment
    that never sets them behaves exactly as it did before this change.

Each leg is asserted at BOTH levels — the module-level resolution and the kwargs
actually handed to the client — because resolving the variable correctly and then
ignoring it at the call site is precisely the failure this guard exists to catch.
"""

from __future__ import annotations

import importlib
import os

import pytest

from app import agents

#: The vendor endpoint the leg defaults to when nothing is configured.
OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"

INFERHUB_BASE = "https://api.inferhub.dev/v1"
OR_KEY = "sk-or-fake-for-guard"
FB_KEY = "sk-ih-fake-for-guard"

_ENV_KEYS = ("FALLBACK_API_BASE", "FALLBACK_API_KEY", "OPENROUTER_API_KEY")


class _Captured(Exception):
    """Raised from the spy so the assertion sees the constructor kwargs and nothing
    after them — the model build past this point is not under test."""


@pytest.fixture
def restore_agents_module():
    """``app.agents`` resolves its env at IMPORT time, so the guards reload it.

    Reload rebinds the module object in place, so any other module holding a
    reference to it still sees a consistent module. Restore the ambient env and
    reload once more on the way out, so no later test inherits these values.
    """
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    try:
        yield
    finally:
        for k in _ENV_KEYS:
            if saved[k] is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = saved[k]
        importlib.reload(agents)


def _built_client_kwargs(monkeypatch):
    """Call the fallback model factory and return the kwargs given to its client.

    Patched rather than real: the HTTP client construction is not under test here,
    only which endpoint and credential the leg asks for.
    """
    captured: dict = {}

    def _spy(**kwargs):
        captured.update(kwargs)
        raise _Captured

    monkeypatch.setattr(agents, "AsyncOpenAI", _spy, raising=False)
    monkeypatch.setattr(agents, "_create_llm_client", lambda timeout: None, raising=False)
    with pytest.raises(_Captured):
        agents._create_openrouter_model("some-fallback-model")
    return captured


def test_fallback_provider_set_is_honoured(monkeypatch, restore_agents_module):
    """Both variables set -> the leg must use them, at resolution and at the call."""
    monkeypatch.setenv("FALLBACK_API_BASE", INFERHUB_BASE)
    monkeypatch.setenv("FALLBACK_API_KEY", FB_KEY)
    monkeypatch.setenv("OPENROUTER_API_KEY", OR_KEY)
    importlib.reload(agents)

    # getattr with a default so a missing name is a REAL assertion failure, not an
    # AttributeError — a crash proves nothing about the property under test.
    assert getattr(agents, "FALLBACK_API_BASE", None) == INFERHUB_BASE, (
        "FALLBACK_API_BASE is not honoured: the module resolves "
        f"{getattr(agents, 'FALLBACK_API_BASE', None)!r}, expected {INFERHUB_BASE!r}. "
        "A fallback leg that cannot be re-pointed is stuck at 0% availability for "
        "the whole duration of a provider outage (measured 2026-09-29)."
    )
    assert getattr(agents, "FALLBACK_API_KEY", None) == FB_KEY, (
        "FALLBACK_API_KEY is not honoured: the module resolves "
        f"{getattr(agents, 'FALLBACK_API_KEY', None)!r}, expected the configured key."
    )

    kwargs = _built_client_kwargs(monkeypatch)
    assert kwargs.get("base_url") == INFERHUB_BASE, (
        "the built fallback client still points at "
        f"{kwargs.get('base_url')!r}, not the configured {INFERHUB_BASE!r}: the "
        "variable is resolved but ignored at the call site, which is the exact "
        "shape that left this leg unmovable during the OpenRouter outage."
    )
    assert kwargs.get("api_key") == FB_KEY, (
        f"the built fallback client authenticates with the wrong credential "
        f"({kwargs.get('api_key')!r}): it must use FALLBACK_API_KEY when set, or a "
        "re-pointed leg sends the old vendor's key to the new endpoint and fails "
        "authentication."
    )


def test_fallback_provider_unset_keeps_openrouter_defaults(
    monkeypatch, restore_agents_module
):
    """Neither variable set -> the OpenRouter defaults must survive unchanged.

    This is the compatibility half: the change must not break a deployment that
    sets nothing, and it is also the leg that pins the default endpoint in one
    place instead of a literal repeated at the call site.
    """
    monkeypatch.delenv("FALLBACK_API_BASE", raising=False)
    monkeypatch.delenv("FALLBACK_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", OR_KEY)
    importlib.reload(agents)

    assert getattr(agents, "FALLBACK_API_BASE", None) == OPENROUTER_DEFAULT_BASE, (
        "with FALLBACK_API_BASE unset the leg must keep the OpenRouter default, but "
        f"resolves {getattr(agents, 'FALLBACK_API_BASE', None)!r}. The default is "
        "what keeps an unconfigured deployment working; dropping it changes behaviour "
        "for every host that never set the variable."
    )
    assert getattr(agents, "FALLBACK_API_KEY", None) == OR_KEY, (
        "with FALLBACK_API_KEY unset the leg must fall through to "
        f"OPENROUTER_API_KEY, but resolves {getattr(agents, 'FALLBACK_API_KEY', None)!r}."
    )

    kwargs = _built_client_kwargs(monkeypatch)
    assert kwargs.get("base_url") == OPENROUTER_DEFAULT_BASE, (
        "unset configuration must still build an OpenRouter-backed client; got "
        f"{kwargs.get('base_url')!r}."
    )
    assert kwargs.get("api_key") == OR_KEY, (
        f"unset configuration must still authenticate with OPENROUTER_API_KEY; got "
        f"{kwargs.get('api_key')!r}."
    )
