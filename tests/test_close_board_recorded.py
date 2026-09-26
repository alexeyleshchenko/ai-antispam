#!/usr/bin/env python3
"""Gate: a close records the BOARD close, not only the ledger row.

Ported from the kit's `TEMPLATE/tests/test_close_board_recorded.py`
(md5 7e0cb27480149d068c51897b79d9c5bc), with ONE deliberate change: the boundary is
this factory's own adoption instant, not the kit's.

**Why our own anchor.** The kit's `INVARIANT_LANDED` is 2026-09-18T18:04:24Z (commit
d6c9d55) — the moment the rule landed in ITS tree. Applied to our ledger that anchor
governs 6 close rows and reds 5 of them: n=19 (#35), n=23 (#41), n=27
(campaign-tg-login-recovery), n=39 (#47), n=44 (#49). Every one was written after the
rule existed elsewhere but before any gate existed HERE. A verbatim port would ship a
gate that is red on arrival with no exemption surface — the #45 class, recreated by
the fix for #39. So the boundary is ours, declared at the instant this gate lands, and
it is FORWARD-ONLY. Nothing is backfilled.

**Origin.** 2026-09-25: HQ filed #49, wrote the full ledger lifecycle (intake n=42,
claim n=43, close n=44) and never closed the board issue. The close row's own prose
read "CLOSED 2026-09-25" while the issue sat state=OPEN, closedAt=null, 0 comments.
Triage's hourly sweep re-derived it as outstanding and the gated trigger FIRED on it.
Interval: 13974 s = 3h52m54s, i.e. 2.94x the kit's worst recorded instance (79m06s).
Triage closed the board at 2026-09-26T00:38:14Z and wrote NO second ledger row — the
lifecycle was already complete, so a second close is a duplicate sequence, not a
repair. Recorded on #40.

The rule, in two parts:

1. **Every `close` row written on or after the invariant landed carries
   `board=closed`** — the board state the settling lane recorded at close time. A
   close that skipped the board close leaves the token absent, so the absence is the
   signal. The value must be `closed`: a close row asserting `board=open` is a
   self-contradiction, not a weaker form of the same record.
2. **Rows written before the invariant are excused, and said so.** They are reported
   as `excused:` with their count, never folded into a bare "clean" — so that
   "clean" and "excused" are never the same output. Nothing is backfilled: a token
   written today for a close that predates the rule would be a falsified record, not
   a repair.

**Why the row records the state rather than the gate checking it.** A gate cannot
call `gh`: the mechanical suite must run offline and against a tree, not a live board.
So the settling lane records the board state it observed, and the gate asserts it was
recorded.

**What this gate does NOT do.** It reads the ROW, never the board. The opposite
direction — a board item closed with no ledger row (the #36/#37/#38 class) — needs a
live board read and belongs to a patrol, not to an offline gate.

The invariant is factored into `close_board_problems()` so synthetic rows can probe
it: a rule that has only ever seen good input has not been shown to reject bad input.

Run:  python3 tests/test_close_board_recorded.py            (script mode — the audit's form)
      python3 -m pytest tests/test_close_board_recorded.py -q
Exit: 0 clean or fully excused, non-zero on any post-invariant close without the token.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "evidence" / "ledger.jsonl"

# THIS FACTORY's boundary: the instant this gate landed here. Rows written before it
# predate the rule and are excused; rows at or after it must carry the token. The
# kit's own constant is deliberately NOT used — see the module docstring.
INVARIANT_LANDED = "2026-09-26T15:25:00Z"
BOARD_TOKEN = "board=closed"


def _parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _has_board_token(detail: str) -> bool:
    """True when `detail` carries a `board=<state>` token, whatever the state."""
    for token in detail.replace(",", " ").replace(";", " ").split():
        if token.startswith("board="):
            return True
    return False


def close_board_problems(
    rows: list[dict], exempt_before: str = INVARIANT_LANDED
) -> tuple[list[str], list[str]]:
    """Return (problems, excused) for the `close` rows of a ledger.

    `problems` names every post-invariant close row missing the token; `excused`
    names every pre-invariant row, so the two are never conflated.
    """
    boundary = _parse_ts(exempt_before)
    problems: list[str] = []
    excused: list[str] = []

    for row in rows:
        if row.get("event") != "close":
            continue
        n, ts = row.get("n"), row.get("ts", "")
        try:
            when = _parse_ts(ts)
        except (ValueError, TypeError):
            problems.append(f"n={n}: unparseable ts {ts!r}")
            continue
        if when < boundary:
            excused.append(f"n={n} ({ts}) predates the invariant ({exempt_before})")
            continue
        detail = str(row.get("detail") or "")
        if BOARD_TOKEN in detail:
            continue
        if _has_board_token(detail):
            problems.append(
                f"n={n} ({ts}) carries a board= token whose value is not `closed` — "
                f"a close row asserting the board is still open contradicts itself"
            )
        else:
            problems.append(
                f"n={n} ({ts}) missing {BOARD_TOKEN} — the board issue was not closed "
                f"with this close, so the ledger says done while the board says open"
            )

    return problems, excused


def _load_rows() -> list[dict]:
    rows: list[dict] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


# --- live gate -------------------------------------------------------------------


def test_live_ledger_records_the_board_close() -> None:
    rows = _load_rows()
    problems, excused = close_board_problems(rows)
    for line in excused:
        print(f"  excused: {line}")
    if problems:
        raise AssertionError(
            "close rows written after the board-close requirement must carry "
            f"{BOARD_TOKEN}:\n  " + "\n  ".join(problems)
        )
    checked = sum(
        1
        for r in rows
        if r.get("event") == "close"
        and _parse_ts(r.get("ts", "")) >= _parse_ts(INVARIANT_LANDED)
    )
    print(
        f"board-close gate: {checked} post-invariant close row(s) verified, "
        f"{len(excused)} excused (pre-invariant)"
    )


# --- probes: the invariant must reject bad input, not only accept good ------------


def test_a_post_invariant_close_without_the_token_is_rejected() -> None:
    rows = [
        {"n": 1, "ts": "2026-09-27T09:00:00Z", "event": "close", "detail": "outcome=accepted"}
    ]
    problems, excused = close_board_problems(rows)
    assert problems and not excused, (problems, excused)
    assert BOARD_TOKEN in problems[0]


def test_a_pre_invariant_close_is_excused_not_failed() -> None:
    rows = [
        {"n": 2, "ts": "2026-09-26T09:00:00Z", "event": "close", "detail": "outcome=accepted"}
    ]
    problems, excused = close_board_problems(rows)
    assert not problems and excused, (problems, excused)


def test_a_token_that_does_not_say_closed_is_rejected() -> None:
    """`board=open` on a close row is a self-contradiction, not a weaker record."""
    rows = [
        {"n": 3, "ts": "2026-09-27T09:00:00Z", "event": "close", "detail": "board=open"}
    ]
    problems, _excused = close_board_problems(rows)
    assert problems, "a board=open close row was accepted"
    assert "contradicts itself" in problems[0]


def test_a_post_invariant_close_with_the_token_passes() -> None:
    rows = [
        {
            "n": 4,
            "ts": "2026-09-27T09:00:00Z",
            "event": "close",
            "detail": f"outcome=accepted {BOARD_TOKEN} gate=all-pass",
        }
    ]
    problems, excused = close_board_problems(rows)
    assert not problems and not excused, (problems, excused)


def test_non_close_rows_are_outside_the_population() -> None:
    """Only `close` rows promise a board close; a claim or run row does not."""
    rows = [
        {"n": 5, "ts": "2026-09-27T09:00:00Z", "event": "claim", "detail": "no token here"},
        {"n": 6, "ts": "2026-09-27T09:00:00Z", "event": "run", "detail": "no token here"},
    ]
    problems, excused = close_board_problems(rows)
    assert not problems and not excused, (problems, excused)


def test_an_unparseable_ts_is_a_problem_not_an_excuse() -> None:
    """A row whose timestamp cannot be read cannot be excused by it either."""
    rows = [{"n": 7, "ts": "not-a-time", "event": "close", "detail": "board=closed"}]
    problems, excused = close_board_problems(rows)
    assert problems and not excused, (problems, excused)


def test_gate_is_registered_in_the_audit() -> None:
    audit = (REPO / "tools" / "audit.py").read_text(encoding="utf-8")
    assert "test_close_board_recorded.py" in audit, (
        "gate not registered in tools/audit.py — an unregistered gate never runs (P29)"
    )


def main() -> int:
    try:
        test_live_ledger_records_the_board_close()
    except AssertionError as exc:
        print(f"close-board gate failed:\n{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
