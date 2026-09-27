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

**The exemption surface, and why it lives in data rather than source.** A close row whose
repair space is EMPTY — written, committed, immutable, in an append-only ledger with no
`--ts` — cannot be corrected, and a gate with no exemption surface makes such a row a
permanent red that blocks the whole deploy path. So the gate reads
`docs/ledger-close-board-exemptions.json`, keyed on ROW IDENTITY (`n` + `ts`, never a
commit sha — the gate validates the live file, so a sha would pin the transport instead of
the row). The list lives in factory data, not inside this file, for the same reason the
kit's five precedents do: this file is copied byte-identically into `TEMPLATE/`, so an
inline table naming one factory's rows would ship to every new factory with the file.

Three outputs, never two: `clean` (no violations, no exemptions), `excused` (every
admitted entry printed with its own reason and the ruling that admitted it, on EVERY run,
so it is a visible debt rather than forgiveness), and `stale` (an entry matching no
violation — a gate ERROR, because an exemption that excuses nothing while inflating
visible debt is worse than no entry). A malformed entry, or one that does not name who
ruled the admission, is likewise a problem rather than a silent pass.

An exemption is a dated, ATTRIBUTED admission. Its `reason` states the ground in its own
words and `admission_ruled_by` names the ruling — so a reader can always tell which half
of an entry is a lane's mechanism and which is a governance decision. The present two
entries excuse `n=54` (#50) and `n=59` (#46) on the narrow true ground that the ORDERING
requirement was unstated at write time; not "unsatisfiable", which would be too strong.
The anchor is unchanged and nothing is backfilled — moving the boundary past those rows
would make the marker's own stated definition false and convert a live violation into a
silent excuse.

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

# Factory data, never source inside this gate: an inline table excusing one
# factory's rows would ship to every new factory with the file. Same reason the
# kit keeps its five exemption lists in docs/*.example.json.
EXEMPTIONS_PATH = "docs/ledger-close-board-exemptions.json"


def _parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _has_board_token(detail: str) -> bool:
    """True when `detail` carries a `board=<state>` token, whatever the state."""
    for token in detail.replace(",", " ").replace(";", " ").split():
        if token.startswith("board="):
            return True
    return False


def _row_key(n: object, ts: object) -> str:
    """A row's identity for exemption matching: `n` + `ts`, never a commit sha.

    The gate validates the LIVE file, so a sha would pin the transport rather than
    the row — a rebase or a re-push would silently stale the entry.
    """
    return f"{n}@{ts}"


def load_exemptions(path: Path) -> tuple[dict[str, dict], list[str]]:
    """`({row_key: entry}, problems)` read from the factory data file.

    Absent means no exemptions — the shipped state. Anything malformed is a
    problem, never a silent pass: an exemption list that quietly fails to load is
    indistinguishable from no exemptions, which is the vacuous-pass shape this
    gate exists to forbid.
    """
    problems: list[str] = []
    if not path.is_file():
        return {}, problems
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [f"{path.name} is not JSON: {exc}"]
    if not isinstance(data, dict):
        return {}, [f"{path.name}: expected a JSON object with an 'exemptions' list"]
    raw = data.get("exemptions", [])
    if not isinstance(raw, list):
        return {}, [f"{path.name}: 'exemptions' must be a list"]

    entries: dict[str, dict] = {}
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            problems.append(f"{path.name}: entry {i} is not an object")
            continue
        n, ts = item.get("n"), item.get("ts")
        if not isinstance(n, int) or not isinstance(ts, str) or not ts:
            problems.append(
                f"{path.name}: entry {i} must carry an integer `n` and a string `ts`: "
                f"n={n!r} ts={ts!r}"
            )
            continue
        if not item.get("reason"):
            problems.append(
                f"{path.name}: entry {i} ({_row_key(n, ts)}) carries no reason"
            )
            continue
        if not item.get("admission_ruled_by"):
            problems.append(
                f"{path.name}: entry {i} ({_row_key(n, ts)}) does not name who ruled the "
                f"admission — a mechanism a lane may write, but an admission is a ruling"
            )
            continue
        key = _row_key(n, ts)
        if key in entries:
            problems.append(f"{path.name}: entry {i} repeats {key}")
            continue
        entries[key] = item
    return entries, problems


def close_board_problems(
    rows: list[dict],
    exempt_before: str = INVARIANT_LANDED,
    exemptions: dict[str, dict] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """Return `(problems, excused, stale)` for the `close` rows of a ledger.

    `problems` names every post-invariant close row missing the token; `excused`
    names rows excused by the boundary OR by an admitted exemption, so the two are
    never conflated with a clean run; `stale` names exemptions that matched no
    violation, because an exemption that silently excuses nothing is visible debt
    that is not being paid down.
    """
    boundary = _parse_ts(exempt_before)
    admitted = dict(exemptions or {})
    matched: set[str] = set()
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
        key = _row_key(n, ts)
        if key in admitted:
            entry = admitted[key]
            matched.add(key)
            excused.append(
                f"n={n} ({ts}) — {entry.get('reason', '')} "
                f"[admission: {entry.get('admission_ruled_by', '')}]"
            )
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

    stale = sorted(set(admitted) - matched)
    return problems, excused, stale


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
    exemptions, load_problems = load_exemptions(REPO / EXEMPTIONS_PATH)
    problems, excused, stale = close_board_problems(rows, exemptions=exemptions)
    problems = list(load_problems) + list(problems)
    for key in stale:
        problems.append(
            f"exemption {key} matches no violation — a stale exemption excuses nothing "
            f"while inflating visible debt; remove it or correct the key"
        )
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
    admitted = len(excused) - sum(
        1 for line in excused if "predates the invariant" in line
    )
    if excused:
        print(
            f"board-close gate: {checked} post-invariant close row(s) verified, "
            f"{len(excused)} excused ({admitted} admitted, "
            f"{len(excused) - admitted} pre-invariant) — excused is a visible debt, "
            f"not a clean run"
        )
    else:
        print(
            f"board-close gate: clean — {checked} post-invariant close row(s) verified"
        )


# --- probes: the invariant must reject bad input, not only accept good ------------


def test_a_post_invariant_close_without_the_token_is_rejected() -> None:
    rows = [
        {"n": 1, "ts": "2026-09-27T09:00:00Z", "event": "close", "detail": "outcome=accepted"}
    ]
    problems, excused, _stale = close_board_problems(rows)
    assert problems and not excused, (problems, excused)
    assert BOARD_TOKEN in problems[0]


def test_a_pre_invariant_close_is_excused_not_failed() -> None:
    rows = [
        {"n": 2, "ts": "2026-09-26T09:00:00Z", "event": "close", "detail": "outcome=accepted"}
    ]
    problems, excused, _stale = close_board_problems(rows)
    assert not problems and excused, (problems, excused)


def test_a_token_that_does_not_say_closed_is_rejected() -> None:
    """`board=open` on a close row is a self-contradiction, not a weaker record."""
    rows = [
        {"n": 3, "ts": "2026-09-27T09:00:00Z", "event": "close", "detail": "board=open"}
    ]
    problems, _excused, _stale = close_board_problems(rows)
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
    problems, excused, _stale = close_board_problems(rows)
    assert not problems and not excused, (problems, excused)


def test_non_close_rows_are_outside_the_population() -> None:
    """Only `close` rows promise a board close; a claim or run row does not."""
    rows = [
        {"n": 5, "ts": "2026-09-27T09:00:00Z", "event": "claim", "detail": "no token here"},
        {"n": 6, "ts": "2026-09-27T09:00:00Z", "event": "run", "detail": "no token here"},
    ]
    problems, excused, _stale = close_board_problems(rows)
    assert not problems and not excused, (problems, excused)


def test_an_unparseable_ts_is_a_problem_not_an_excuse() -> None:
    """A row whose timestamp cannot be read cannot be excused by it either."""
    rows = [{"n": 7, "ts": "not-a-time", "event": "close", "detail": "board=closed"}]
    problems, excused, _stale = close_board_problems(rows)
    assert problems and not excused, (problems, excused)


def test_an_admitted_exemption_is_excused_loudly_not_clean() -> None:
    """An exemption excuses visibly — `clean` and `excused` must never be one output."""
    rows = [
        {
            "n": 54,
            "ts": "2026-09-27T09:00:00Z",
            "event": "close",
            "detail": "outcome=accepted",
        }
    ]
    exemptions = {
        "54@2026-09-27T09:00:00Z": {
            "n": 54,
            "ts": "2026-09-27T09:00:00Z",
            "reason": "ordering requirement unstated at write time",
            "admission_ruled_by": "ai-antispam HQ 2026-09-27",
        }
    }
    problems, excused, stale = close_board_problems(rows, exemptions=exemptions)
    assert not problems, problems
    assert excused, "an admitted exemption was not reported as excused"
    assert "unstated at write time" in excused[0], (
        "the entry's own reason is not printed"
    )
    assert "ai-antispam HQ 2026-09-27" in excused[0], "the admission is not attributed"
    assert not stale, stale


def test_an_exemption_matching_no_violation_is_an_error() -> None:
    """A stale exemption excuses nothing while inflating visible debt."""
    rows = [
        {
            "n": 9,
            "ts": "2026-09-27T09:00:00Z",
            "event": "close",
            "detail": "outcome=accepted board=closed",
        }
    ]
    exemptions = {
        "54@2026-09-27T09:00:00Z": {
            "n": 54,
            "ts": "2026-09-27T09:00:00Z",
            "reason": "x",
            "admission_ruled_by": "y",
        }
    }
    problems, _excused, stale = close_board_problems(rows, exemptions=exemptions)
    assert not problems, problems
    assert stale == ["54@2026-09-27T09:00:00Z"], stale


def test_the_live_exemption_file_loads_and_every_entry_is_used() -> None:
    """The shipped data file must parse, and no entry may sit there unused."""
    exemptions, problems = load_exemptions(REPO / EXEMPTIONS_PATH)
    assert not problems, problems
    _p, _e, stale = close_board_problems(_load_rows(), exemptions=exemptions)
    assert not stale, f"the live exemption file carries a stale entry: {stale}"


def test_gate_is_registered_in_the_audit() -> None:
    audit = (REPO / "tools" / "audit.py").read_text(encoding="utf-8")
    assert "test_close_board_recorded.py" in audit, (
        "gate not registered in tools/audit.py — an unregistered gate never runs (P29)"
    )


def main() -> int:
    rows = _load_rows()
    exemptions, load_problems = load_exemptions(REPO / EXEMPTIONS_PATH)
    problems, excused, stale = close_board_problems(rows, exemptions=exemptions)
    problems = list(load_problems) + list(problems)
    for key in stale:
        problems.append(
            f"exemption {key} matches no violation — a stale exemption excuses nothing "
            f"while inflating visible debt; remove it or correct the key"
        )
    for line in excused:
        print(f"  excused: {line}")
    if problems:
        print("close-board gate failed:", file=sys.stderr)
        for line in problems:
            print(f"  {line}", file=sys.stderr)
        return 1
    admitted = len(excused) - sum(
        1 for line in excused if "predates the invariant" in line
    )
    if excused:
        print(
            f"close-board gate: excused — {len(excused)} row(s) ({admitted} admitted, "
            f"{len(excused) - admitted} pre-invariant); this is a visible debt, "
            f"not a clean run"
        )
    else:
        print(
            "close-board gate: clean — every post-invariant close row carries board=closed"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
