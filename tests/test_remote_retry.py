"""The transport-retry contract shared by the two SSH probes.

Both ``scripts/loss_watch.py`` and ``scripts/bot_health_probe.py`` reach the
apps host over ssh. A transient reset of the TCP handshake (sshd's MaxStartups
throttle dropping us during a brute-force burst, measured 2026-10-31
18:44:58Z) is ssh's own rc=255 and says nothing about the bot: single-shot, it
turned into a READ ERROR wake for a store that was healthy the whole time.

The half that must NOT drift is the other one: a remote command that actually
ran and failed returns its own exit code, and retrying it would hide a real
defect behind two more identical failures. Both halves are pinned here.

Run: pytest tests/test_remote_retry.py -v
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture(params=["loss_watch", "bot_health_probe"])
def module(request):
    path = ROOT / "scripts" / f"{request.param}.py"
    spec = importlib.util.spec_from_file_location(request.param, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    # Tests must never sleep the real backoff.
    mod.RETRY_BACKOFF_S = tuple(0 for _ in mod.RETRY_BACKOFF_S)
    return mod

def _scripted(mod, results):
    """Replace mod.sh with a scripted sequence, recording how often it ran."""
    calls: list[str] = []
    seq = list(results)

    def fake_sh(args, timeout=90, stdin=None):
        calls.append(args[-1])
        return seq.pop(0) if len(seq) > 1 else seq[0]

    mod.sh = fake_sh
    return calls

def test_transport_reset_is_retried_then_succeeds(module):
    calls = _scripted(
        module,
        [(255, "", "kex_exchange_identification: read: Connection reset by peer"),
         (0, "1|2|3\n", "")],
    )
    rc, out, _ = module.remote("echo hi")
    assert rc == 0
    assert out == "1|2|3\n"
    assert len(calls) == 2

def test_exhausted_transport_returns_last_failure(module):
    calls = _scripted(module, [(255, "", "Connection reset by peer")])
    rc, _, err = module.remote("echo hi")
    assert rc == 255
    assert "Connection reset" in err
    assert len(calls) == module.RETRY_ATTEMPTS

def test_timeout_is_retried(module):
    calls = _scripted(module, [(124, "", "timeout"), (0, "ok", "")])
    rc, out, _ = module.remote("echo hi")
    assert (rc, out) == (0, "ok")
    assert len(calls) == 2

@pytest.mark.parametrize("rc", [1, 2, 3, 126])
def test_genuine_remote_failure_is_never_retried(module, rc):
    """A command that ran and failed is a verdict, not a transport blip."""
    calls = _scripted(module, [(rc, "", "psql: relation does not exist")])
    got, _, err = module.remote("echo hi")
    assert got == rc
    assert "relation does not exist" in err
    assert len(calls) == 1
