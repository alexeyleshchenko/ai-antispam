"""#152: the CI dummy token must not collide with a fixture's actor id.

An arm that decides *"did the bot do this, or did a human?"* by comparing ids
is only a control while the two ids differ. The CI dummy token's numeric
prefix IS the integer `tests/handlers/test_status_handlers.py` uses for
`HUMAN_ID`, so under CI `bot.id == HUMAN_ID` and the control inverts — in the
PASSING direction, which is the one failure mode a suite cannot notice.

Measured on CI 43965695185: the first run of the #150/#151 branch failed on the
positive control, not on the guard it protected. A human removal read as a
self-leave. Green locally against the real token, `assert 0 == 1` in CI.
Neither reading was about the guard.

The hazard belongs to the PATTERN, not to that one fixture: every future test
that compares an actor id to `bot.id` without pinning inherits it silently. So
this module reads the collision from the WORKFLOW FILE — the token is what
carries the hazard, and a local run's real token cannot reproduce it by
construction. CI is the gate; this test is what makes the gate honest.
"""

import ast
import pathlib
import re

import pytest
from aiogram import Bot

REPO = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github" / "workflows" / "deploy.yml"
TESTS_DIR = REPO / "tests"

# `env:\n  BOT_TOKEN: "<id>:<secret>"` — the workflow's own declaration, not the
# live environment. Reading the env would test the runner, not the tree.
_TOKEN_RE = re.compile(r'^\s*BOT_TOKEN:\s*"([^"]+)"', re.MULTILINE)
# Module-level `<SOMETHING>_ID = <int>`; `_ID` names only, so a fixture's
# unrelated literal (a port, a timeout) is not dragged into the population.
_ID_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*_ID$")


def _workflow_bot_token() -> str:
    """The dummy token the workflow declares for its test run."""
    match = _TOKEN_RE.search(WORKFLOW.read_text(encoding="utf-8"))
    assert match, f"BOT_TOKEN not declared in {WORKFLOW}"
    return match.group(1)


def _token_prefix(token: str) -> int:
    """`123456789:ABC…` -> 123456789 — what aiogram derives `bot.id` from."""
    head, _, _ = token.partition(":")
    return int(head)


def _fixed_id_constants() -> dict[str, tuple[str, int]]:
    """Every module-level `*_ID = <int>` under `tests/`, with its file.

    AST, not a regex or a path:line list: a path:line enumeration goes stale on
    the first refactor and then reports a clean zero from a broken instrument,
    which is the failure this test exists to prevent.
    """
    found: dict[str, tuple[str, int]] = {}
    for path in sorted(TESTS_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if not isinstance(value, ast.Constant) or isinstance(value.value, bool):
                continue
            if not isinstance(value.value, int):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and _ID_NAME_RE.match(target.id):
                    found[target.id] = (str(path.relative_to(REPO)), value.value)
    return found


def _collisions(prefix: int, constants: dict[str, tuple[str, int]]) -> dict[str, str]:
    """Names whose literal equals `prefix`. Factored out so it can be bitten."""
    return {name: loc for name, loc in constants.items() if loc[1] == prefix}


def test_the_workflow_token_is_parsed_not_assumed() -> None:
    """Non-vacuity: the guard below is meaningless if the parse silently fails."""
    token = _workflow_bot_token()
    assert ":" in token, f"expected '<id>:<secret>', read {token!r}"
    assert _token_prefix(token) > 0


def test_the_constant_scan_finds_the_constants_that_matter() -> None:
    """The population is real: `HUMAN_ID` and `BOT_ID` must both be in it.

    Without this, a scan that silently matched nothing would report the same
    clean result as a tree with no collision — the self-consistent marker.
    """
    constants = _fixed_id_constants()
    assert "HUMAN_ID" in constants, sorted(constants)
    assert "BOT_ID" in constants, sorted(constants)
    assert constants["HUMAN_ID"][1] != constants["BOT_ID"][1], (
        "the two actor constants must differ from each other"
    )


def test_ci_token_prefix_collides_with_no_fixed_id_constant() -> None:
    """#152 close condition 1: the collision is asserted, not assumed.

    In CI `bot.id` is derived from this token, so any `*_ID` literal equal to
    its prefix makes a fixture's actor indistinguishable from the bot.
    """
    prefix = _token_prefix(_workflow_bot_token())
    collisions = _collisions(prefix, _fixed_id_constants())
    assert collisions == {}, (
        f"the CI dummy token's prefix {prefix} collides with "
        f"{collisions} — a control keyed on actor identity would read a human "
        "as the bot. Move the token's prefix, never the constant (#152)."
    )


def test_the_guard_bites() -> None:
    """Bite proof: a colliding constant set is reported, not silently passed.

    A guard that cannot fail is worse than none, and this one's whole subject
    is a control that inverted while looking green.
    """
    prefix = _token_prefix(_workflow_bot_token())
    poisoned = {"HUMAN_ID": ("tests/fake.py", prefix)}
    assert _collisions(prefix, poisoned) == {"HUMAN_ID": ("tests/fake.py", prefix)}
    assert _collisions(prefix, {"BOT_ID": ("tests/fake.py", prefix + 1)}) == {}


def test_aiogram_derives_bot_id_from_the_token() -> None:
    """The vector itself: `bot.id` is the token prefix, not a fixture's choice.

    This is why the guard must read the workflow file — the identity under CI
    is a property of the declared token.
    """
    token = _workflow_bot_token()
    assert Bot(token=token).id == _token_prefix(token)


@pytest.mark.parametrize("name", ["HUMAN_ID", "BOT_ID"])
def test_actor_constants_are_distinct_from_the_ci_bot(name: str) -> None:
    """Restates the invariant at the constant level, so a failure names the id."""
    constants = _fixed_id_constants()
    prefix = _token_prefix(_workflow_bot_token())
    assert constants[name][1] != prefix, (
        f"{name} ({constants[name][0]}) equals the CI bot id {prefix}"
    )
