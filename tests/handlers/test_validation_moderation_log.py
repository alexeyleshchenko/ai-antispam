"""Guard for the moderation-disabled group log line (issue #58).

MEASURED DEFECT: a message arriving in a group whose ``moderation_enabled`` is
false was dropped with NO log line — the trace stopped after ``get_group`` and
the pipeline simply ended. Diagnosing a 12-hour zero-verdict window cost roughly
an hour: the window was the designed consequence of two groups being moderation
disabled and nothing said so.

The sibling early-exit three lines above logs at INFO, and the docstring names
both exits symmetrically — which is what made the silence an inconsistency
rather than a policy.

The RATE is why a straight copy of the sibling does not work: those two groups
produced 425 updates in 12 hours, so the line is emitted on the FIRST sighting of
a state and stays silent while the state is steady.

Guarded here, matching the close condition's own wording:

  1. the first message from a disabled group emits exactly one INFO line naming
     the group and the exit reason;
  2. a second message in the same steady state emits NO further line;
  3. a state change (disabled -> enabled -> disabled) emits again;
  4. the returned reason is ``message_moderation_disabled`` and an enabled group
     is returned untouched.

Against the pre-fix revision leg 1 fails with zero records — the defect verbatim
— so this guard does not pass vacuously on the revision it was written for.
"""

import logging

import pytest

from app.handlers.message import validation

# Imported through the module (not the symbol) so this guard still RUNS on a
# revision that predates the state cache: the fail-first proof must be an
# assertion about behaviour, not an ImportError about a missing name.
get_and_check_group = validation.get_and_check_group


def _state_cache() -> dict:
    return getattr(validation, "_moderation_state_cache", {})

LOGGER_NAME = "app.handlers.message.validation"
CHAT_ID = -1001822193445
DISABLED_LINE = "Group moderation disabled for chat"


class _FakeGroup:
    def __init__(self, moderation_enabled: bool) -> None:
        self.moderation_enabled = moderation_enabled


def _captured_lines(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER_NAME and DISABLED_LINE in record.getMessage()
    ]


@pytest.fixture(autouse=True)
def _isolate_state_cache():
    _state_cache().clear()
    yield
    _state_cache().clear()


async def _arrive(monkeypatch, caplog, moderation_enabled: bool):
    """Drive one message through the group check and return what it logged."""

    async def _fake_get_group(_chat_id: int):
        return _FakeGroup(moderation_enabled)

    monkeypatch.setattr(
        "app.handlers.message.validation.get_group", _fake_get_group
    )
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        group, reason = await get_and_check_group(CHAT_ID, "Test Group", "testgroup")
    return group, reason, _captured_lines(caplog)


async def test_first_sighting_logs_exactly_one_info_line(monkeypatch, caplog):
    group, reason, lines = await _arrive(monkeypatch, caplog, False)

    assert group is None
    assert reason == "message_moderation_disabled"
    assert len(lines) == 1, f"expected one line on first sighting, got {lines}"
    assert str(CHAT_ID) in lines[0]

    record = next(r for r in caplog.records if DISABLED_LINE in r.getMessage())
    assert record.levelno == logging.INFO


async def test_steady_disabled_state_adds_no_line(monkeypatch, caplog):
    await _arrive(monkeypatch, caplog, False)
    _, _, lines = await _arrive(monkeypatch, caplog, False)

    assert lines == [], f"steady state emitted a line: {lines}"


async def test_state_change_logs_again(monkeypatch, caplog):
    await _arrive(monkeypatch, caplog, False)
    await _arrive(monkeypatch, caplog, True)
    _, _, lines = await _arrive(monkeypatch, caplog, False)

    assert len(lines) == 1, f"state change did not re-log: {lines}"


async def test_enabled_group_is_returned_and_never_logged(monkeypatch, caplog):
    group, reason, lines = await _arrive(monkeypatch, caplog, True)

    assert group is not None
    assert reason == ""
    assert lines == []
