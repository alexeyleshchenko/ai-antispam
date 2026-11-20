#!/usr/bin/env python3
"""Gate: every ledger event that has ROWS carries an AUTHORIZATION (2026-11-20).

`AUTHORIZED_ACTORS_BY_EVENT` is the role-to-event matrix. Two surfaces consume it,
and BOTH fail open on an event it does not name:

  * the write path — `tools/ledger.py:1207`,
    `if authorized and actor_origin == "derived" and args.actor not in authorized`
  * this tree's schema gate — `tests/test_ledger_schema.py:197`,
    `if actor and event and allowed_actors and actor not in allowed_actors`

An empty tuple is falsy, so an event with NO matrix entry is never checked on
either surface. The row is written, the gate passes, and nothing anywhere says
the event was ungated. That is the INVERSE of the #156 unauthorized-intake class:
not a lane writing past its authorization, but a lifecycle event that no
authorization governs at all.

Measured 2026-11-20: `release` carried **20 rows** (`hq` 15, `triage` 5) and no
matrix entry, in this factory and in the kit. A `release` WITHDRAWS another
lane's claim, and `verify`'s open-claim answer is the thing that tells a reader
in-flight from abandoned — so an ungated release can silence that signal, and
did so unchecked for every one of the 20.

This gate holds the CLASS, not the one instance: it reads the events that
actually appear in the ledger and requires each to resolve to a non-empty actor
set. A future event added without a matrix entry reds here on the day its first
row is written, instead of never.

FACTORY-LOCAL, NOT TEMPLATE LAW (2026-11-20). The kit already ships this class
gate: `TEMPLATE/tests/test_declared_event_population.py`, landed `8e5fc278` on
2026-10-21 for #526, declared in `REQUIRED_GATES` and in the kit audit's run
list. It is absent here because this tree's pin predates that commit and the
kit's declared-set table never named the gate (kit #659, ruled T0), not because
the class was uncovered. Do NOT offer this file upstream as the template half:
its second arm is this factory's own decision (`release` is the AUTHORITY set
`{hq, triage, owner}`, and `worker` may claim and must not release), which each
factory sets in its own declaration. Keep the class arm here only as a local
control until the kit gate is ported.

Run:  python3 -m pytest tests/test_ledger_event_authorized.py -q
Exit: 0 clean, non-zero on an ungated event or a release by an unauthorized actor.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "evidence" / "ledger.jsonl"
sys.path.insert(0, str(REPO / "tools"))

from ledger_declaration import authorized_for_event


def _rows() -> list[dict]:
    return [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]


def _event_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        event = row.get("event")
        if isinstance(event, str) and event:
            counts[event] = counts.get(event, 0) + 1
    return counts


def ungated_events(rows: list[dict], repo: Path = REPO) -> list[str]:
    """Events with rows and NO authorized actor — the fail-open population.

    `authorized_for_event` is the ONE home both consumers read, so a violation
    here is a violation on the write path and in the schema gate at once.
    """
    return sorted(e for e in _event_counts(rows) if not authorized_for_event(repo, e))


def test_no_event_with_rows_is_ungated() -> None:
    rows = _rows()
    assert rows, "the ledger is empty — this gate would pass vacuously"
    ungated = ungated_events(rows)
    counts = _event_counts(rows)
    assert not ungated, (
        "event(s) carry rows but resolve to NO authorized actor, so both the write "
        "path and the schema gate skip them entirely: "
        + ", ".join(f"{e} ({counts[e]} row(s))" for e in ungated)
    )


def test_release_is_authorized_and_excludes_claiming_only_lanes() -> None:
    """The measured instance, held by name.

    `release` withdraws a claim, so its actor set is the AUTHORITY set and not the
    claiming set: a lane that may take a unit may not thereby withdraw another
    lane's. `worker` is the sharp control — it MAY claim and must NOT release.
    """
    allowed = authorized_for_event(REPO, "release")
    assert allowed, "release has no authorized actor — the withdrawal verb is ungated"
    assert set(allowed) == {"hq", "triage", "owner"}, f"release authorized for {allowed}"
    claimable = authorized_for_event(REPO, "claim")
    assert "worker" in claimable, "the control is void: worker cannot claim, so 'worker cannot release' proves nothing"
    assert "worker" not in allowed, "worker may claim AND release — a claim can be withdrawn by any lane holding one"


def test_every_release_row_names_an_authorized_actor() -> None:
    rows = [r for r in _rows() if r.get("event") == "release"]
    assert rows, "no release rows — this arm would pass vacuously"
    allowed = set(authorized_for_event(REPO, "release"))
    offenders = sorted({r.get("actor") for r in rows} - allowed)
    assert not offenders, f"release row(s) by unauthorized actor(s): {offenders}"


def test_the_ungated_check_bites(tmp_path: Path) -> None:
    """NEUTER PROOF: with `release` removed from the declaration, the check REDs.

    A gate that cannot fail on the defect it names is not a gate. This drives the
    same `ungated_events` predicate against a declaration that omits `release` and
    requires it to report exactly that event.
    """
    declaration = tmp_path / "authorizations.json"
    declaration.write_text(
        json.dumps(
            {
                "actors": ["bot", "outreach", "landing"],
                "by_event": {
                    "claim": ["bot", "outreach", "landing"],
                    "close": ["bot", "outreach", "landing"],
                    "run": ["bot", "outreach", "landing"],
                },
            }
        ),
        encoding="utf-8",
    )
    code = (
        "import json, sys, pathlib;"
        f"sys.path.insert(0, {str(REPO / 'tools')!r});"
        "from ledger_declaration import authorized_for_event;"
        f"rows=[json.loads(l) for l in open({str(LEDGER)!r}) if l.strip()];"
        "evs={r.get('event') for r in rows if r.get('event')};"
        f"print('|'.join(sorted(e for e in evs if not authorized_for_event(pathlib.Path({str(REPO)!r}), e))))"
    )
    got = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "OC_AUTHORIZATIONS_PATH": str(declaration)},
        cwd=str(REPO),
    )
    assert got.returncode == 0, got.stderr[-400:]
    assert "release" in got.stdout.strip().split("|"), (
        "removing the release authorization did NOT make the check report it — "
        f"the gate is vacuous. reported ungated: {got.stdout.strip()!r}"
    )
