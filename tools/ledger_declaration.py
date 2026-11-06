#!/usr/bin/env python3
"""The factory's DECLARED ledger policy — ONE home, shared by the tool and the gates.

Two declarations live here, and for the same reason: two consumers must agree about each,
and a second copy would drift on exactly the inputs that matter.

* the INVARIANT BOUNDARIES a factory has adopted, read from `docs/ledger-invariants.json`
  (the rest of this module), and
* the ROLE-TO-EVENT AUTHORIZATION MATRIX (`AUTHORIZED_ACTORS_BY_EVENT`), which the write
  path enforces at append time and the schema gate reports against.

`docs/ledger-invariants.json` maps an invariant's name to the ISO-8601 UTC instant at
which it took effect IN THIS FACTORY. It is factory DATA, so it does not ship; the
skeleton `docs/ledger-invariants.example.json` does — the shape #69 gave
`docs/products.json`.

Two consumers read it, and they must agree about what it says:

* `tests/ledger_boundary.py` — the gates. A row at or after its boundary is GOVERNED, and
  a row before it is EXCUSED and printed as such.
* `tools/ledger.py repair` — the write path. A row before its boundary must be REFUSED,
  because writing a value into a pre-boundary row is the BACKFILL the no-backfill law
  forbids.

The POLICIES differ deliberately: a gate with nothing to judge SKIPS, and a repair with
no boundary to stand on REFUSES. The READING must not differ. Two implementations would
drift on exactly the inputs that matter — a missing key, a malformed value, an
unparseable date — and the factory would then hold a gate that excuses a row the repair
path refuses to correct, or one that governs a row the repair path would happily rewrite.
So the reading lives here, once, and each consumer maps this module's two outcomes onto
its own policy:

* `DeclarationUnavailable` — nothing declared for this key, or no declaration at all.
  The caller decides: SKIP (a gate) or REFUSE (the repair path). Never a silent pass, and
  never another factory's date.
* `DeclarationUnreadable` — the factory declared something it cannot honour. A declared
  parameter that cannot be read is a DEFECT, not an absence (#69 clause (e)), so both
  callers FAIL. It must never be quietly downgraded onto the skip path, which is why the
  two outcomes are separate types rather than one exception carrying a flag.

This module holds no tests and no state of its own: it is the reader, and it is the only
one. `tools/ledger.py` reaches it through a plain import (so the fixture helper
`tests/gate_fixtures.stage_tool` copies it into a throwaway tree by following that
import), and `tests/ledger_boundary.py` adds the tool directory to `sys.path`.

Run:  imported by `tools/ledger.py`, `tests/ledger_boundary.py` and
      `tests/test_ledger_schema.py`, never run directly.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import NamedTuple

DECLARATION_REL = "docs/ledger-invariants.json"
DECLARATION_EXAMPLE_REL = "docs/ledger-invariants.example.json"

# Role-to-Event authorization, in ONE home.
#
# Two consumers must agree about it and neither may hold its own copy: the write path
# (`tools/ledger.py append`) refuses a row the matrix does not authorize, and
# `tests/test_ledger_schema.py` reports one written before that refusal existed. While the
# matrix lived only in the gate, the write path could not consult it, so an unauthorized
# row was written SILENTLY and surfaced a day later — the gate was the only guard, and its
# blind spot was exactly one audit wide by construction.
#
# Membership is not authorization: `known_actors()` answers whether a name is an actor at
# all, and this answers which actor may write which event. `append` checks both, and a
# refusal names the event and the actor it refused.
AUTHORIZED_ACTORS_BY_EVENT: dict[str, tuple[str, ...]] = {
    "genesis": ("hq", "owner"),
    "ruling": ("hq", "owner"),
    "score": ("surveys", "hq", "owner"),
    "intake": ("triage", "hq", "owner", "delegate"),
    "claim": ("hq", "worker", "carrier", "triage", "delegate", "surveys"),
    "dispatch": ("triage", "hq", "owner", "delegate"),
    "close": ("hq", "worker", "carrier", "triage", "delegate", "surveys", "owner"),
    "run": ("hq", "surveys", "worker", "carrier", "triage", "delegate", "owner"),
}


AUTHORIZATIONS_REL = "docs/ledger-authorizations.json"
AUTHORIZATIONS_EXAMPLE_REL = "docs/ledger-authorizations.example.json"

def load_authorizations(repo: Path) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    """`(declared_actors, declared_by_event)` — the factory's OWN lanes and what they may write.

    WHY THIS IS A FILE AND NOT A CONSTANT IN THE TOOL, and the reason is the SAME one that put
    `tools/actors.txt` beside the `ACTORS` tuple: `tools/ledger_declaration.py` is copied into
    every factory, so a lane that only ONE factory has cannot sit in a constant that must match
    everywhere. The matrix had no such seam, which meant a factory's own lane could be DECLARED
    as a lane (the fragment names it, `actors.txt` grants membership) and still be refused at
    append, because the matrix had no row for it and there was no lawful way to give it one.
    Membership and authorization were the two halves of the same declaration, and only one had a
    home (#193).

    A declaration ADDS; it never removes or redefines a core entry. The constant stays the floor,
    so every factory keeps the core matrix whether or not it declares anything.

    ABSENT MEANS NONE, and MALFORMED IS A PROBLEM — the convention every sibling surface states:
    absent or empty means the factory has declared nothing (the shipped state of a new factory),
    while a file that exists and cannot be read must never pass quietly, because a declaration
    that fails to load is indistinguishable from no declaration at all.
    """
    # The override is the isolation seam, the same shape `OC_ACTORS_PATH` uses for
    # membership: a probe that read the LIVE declaration would pass or fail on this
    # factory's own declared lanes instead of on the code.
    path = Path(os.environ.get("OC_AUTHORIZATIONS_PATH", repo / AUTHORIZATIONS_REL))
    if not path.is_file():
        return (), {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DeclarationUnreadable([f"{AUTHORIZATIONS_REL} exists but cannot be read: {exc}"]) from exc
    except json.JSONDecodeError as exc:
        raise DeclarationUnreadable([f"{AUTHORIZATIONS_REL} is not valid JSON: {exc}"]) from exc
    if not isinstance(payload, dict):
        raise DeclarationUnreadable([
            f"{AUTHORIZATIONS_REL} must be a JSON object carrying `actors` and `by_event`"
        ])
    actors = payload.get("actors")
    if not isinstance(actors, list) or not all(isinstance(a, str) and a.strip() for a in actors):
        raise DeclarationUnreadable([
            f"{AUTHORIZATIONS_REL} carries no `actors` list of role names — "
            f"see {AUTHORIZATIONS_EXAMPLE_REL}"
        ])
    by_event = payload.get("by_event", {})
    if not isinstance(by_event, dict):
        raise DeclarationUnreadable([
            f"{AUTHORIZATIONS_REL} `by_event` must be an object mapping event -> [role, ...]"
        ])
    out: dict[str, tuple[str, ...]] = {}
    for event, names in by_event.items():
        if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
            raise DeclarationUnreadable([
                f"{AUTHORIZATIONS_REL} by_event[{event!r}] must be a list of role names"
            ])
        out[str(event)] = tuple(str(n) for n in names)
    return tuple(str(a) for a in actors), out



def authorized_for_event(repo: Path, event: str) -> tuple[str, ...]:
    """The roles authorized for `event` — the core matrix UNION this factory's declaration.

    ONE HOME, because the write path and the schema gate must agree about it: while they
    held separate copies, a declared authorization would pass the tool and red the gate,
    which is the same defect that made `test_ledger_schema.py` carry its own event tuple
    and refuse an event the tool lawfully wrote.

    A declaration ADDS; it never removes or redefines a core entry. So the constant is the
    floor and the declaration extends it, and a factory that declares nothing gets exactly
    the core matrix it has today.
    """
    core = AUTHORIZED_ACTORS_BY_EVENT.get(event, ())
    _, declared = load_authorizations(repo)
    return core + tuple(a for a in declared.get(event, ()) if a not in core)


class DeclarationUnavailable(Exception):
    """This factory has declared nothing for this key — the CALLER decides what that means.

    A gate skips on it (there is nothing to judge); the repair path refuses on it (a row's
    boundary cannot be established, so its correction cannot be shown to be a correction
    rather than a backfill). One reading, two policies.
    """

class DeclarationUnreadable(Exception):
    """The factory declared something it cannot honour: FAIL, never skip."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = list(problems)

def parse_ts(value: str) -> dt.datetime:
    """An ISO-8601 UTC timestamp, as `tools/ledger.py` writes it."""
    return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))

EXEMPTIONS_REL = "docs/ledger-exemptions.json"
EXEMPTIONS_EXAMPLE_REL = "docs/ledger-exemptions.example.json"

RENUMBERS_REL = "docs/ledger-renumbers.json"

class Renumber(NamedTuple):
    """One declared subject renumber, and the PARTITION that makes it lawful.

    `was` and `evidence` are the renumber proper: the number a unit was filed under, and
    the receipt that establishes the step. `prior_board`, `prior_floor` and
    `prior_ceiling` are the REISSUE PARTITION, declared only when the new subject string
    was already OCCUPIED by a DIFFERENT unit — the board it became, and the RANGE of row
    numbers under this subject that belongs to it. All three are None for a renumber into
    a FREE number (#107 <- #74), which is the shape the surface was first built for and
    still the common one.

    WHY THE PARTITION CARRIES AN OPTIONAL FLOOR, stated with its measured status. A
    ceiling alone assumes the prior occupant's rows are a PREFIX of the subject's rows.
    That holds for all four live cases — their prior blocks are n=108..113, n=135..138,
    n=150..163 and n=168..171, and each reissued unit's own legs were filed under its OLD
    subject, so they sit in the OLD subject's bucket and reach a close under the new one
    through the declared HOP, never through the partition. The floor is therefore INERT
    on this ledger today, and that is written here rather than left for a reader to
    discover. It is declared anyway because a ceiling-only language is silently WRONG for
    the first reissued unit that files a row under the NEW subject below the prior
    occupant's block: that row would be classified as the prior occupant's, and the unit
    would be judged against another unit's legs. A partition that can only express
    prefixes is a trap with a delay fuse.

    WHY THE PARTITION IS A DECLARATION AND NOT A DERIVATION, measured 2026-11-06 on this
    ledger: 29 of the 62 migrated items carry pre-migration rows under their NEW number,
    belonging to a different unit — so "the subject's rows" is not one unit's rows, and a
    predicate that assumes it is will read another unit's close as this one's. The
    boundary cannot be inferred: it is a fact about the BOARD history (which item held the
    number when), and no arithmetic over row numbers recovers it. The prior unit's board
    is named beside it so a reader can check the one thing that makes the partition safe —
    that the unit being excluded is CLOSED, i.e. that no live work is being partitioned
    out of sight.
    """
    was: str
    evidence: str
    prior_board: int | None = None
    prior_floor: int | None = None
    prior_ceiling: int | None = None


def load_renumbers(repo: Path) -> dict[str, Renumber]:
    """The DECLARED subject renumbers: new subject -> Renumber(was, evidence, partition).

    WHY THIS EXISTS, measured 2026-11-05. The sequence predicate compares subjects as
    EXACT STRINGS and applies no normalisation -- deliberately, because guessing at
    intent is how a gate starts agreeing with its author. A BOARD RENUMBER breaks that
    premise without breaking the predicate's honesty: the 2026-10-04 board migration
    moved every service item onto a new number, so an item's intake and claim carry the
    number it had when it was FILED while its close carries the number it has NOW. On
    this ledger that is 10 of the 11 held items, and the write path refused the close
    for #107 with *"close for #107 has no intake before it"* while n=147 and n=148 --
    that item's own intake and claim -- sat three inches away under `#74`.

    SO THE ANSWER IS A DECLARATION, NEVER AN INFERENCE. The predicate cannot derive the
    mapping: arithmetic (`N+33`) mis-resolves 8 of the 62 migrated items, and a scan of
    the board would let the gate read its expectation from a live artifact that moves.
    A declared map is the same shape the sibling surfaces use for exemptions,
    retirements and authorizations, and it is PRINTED on every use.

    WHAT THIS CANNOT DO, and the two refusals that keep it honest. A resolution only
    ever FINDS legs that already exist under the named subject; it never manufactures
    one. An entry whose `was` subject carries no legs changes nothing, and the close is
    refused exactly as before. And a malformed file is a PROBLEM rather than a silent
    pass, for the reason the exemptions loader states: *"an exemption list that quietly
    fails to load is indistinguishable from no exemptions."*

    ABSENT MEANS NONE -- a factory that has never renumbered a board has nothing to
    declare, which is the shipped state of a new factory.
    """
    path = repo / RENUMBERS_REL
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DeclarationUnreadable([f"{RENUMBERS_REL} exists but cannot be read: {exc}"]) from exc
    except json.JSONDecodeError as exc:
        raise DeclarationUnreadable([f"{RENUMBERS_REL} is not valid JSON: {exc}"]) from exc
    if not isinstance(payload, dict):
        raise DeclarationUnreadable([
            f"{RENUMBERS_REL} must be a JSON object carrying `renumbers`"])
    rows = payload.get("renumbers")
    if not isinstance(rows, dict):
        raise DeclarationUnreadable([
            f"{RENUMBERS_REL} carries no `renumbers` object -- a map of the subject a row "
            f"carries NOW to the subject it carried when it was filed"])
    out: dict[str, Renumber] = {}
    for subject, entry in rows.items():
        if not isinstance(entry, dict):
            raise DeclarationUnreadable([
                f"{RENUMBERS_REL}: renumbers[{subject!r}] is not an object"])
        missing = [k for k in ("was", "evidence") if not entry.get(k)]
        if missing:
            raise DeclarationUnreadable([
                f"{RENUMBERS_REL}: renumbers[{subject!r}] is missing {', '.join(missing)} "
                f"-- a renumber names the subject it was AND the receipt that establishes it"])
        # THE OPTIONAL PARTITION. Absent is the shipped state and means "the new number
        # was free"; PRESENT but malformed is a typed failure, because a partition that
        # silently failed to parse would read as "free number" and hand the reader the
        # other unit's close — the exact false close the surface exists to prevent.
        prior_board: int | None = None
        prior_floor: int | None = None
        prior_ceiling: int | None = None
        occupant = entry.get("prior_occupant")
        if occupant is not None:
            if not isinstance(occupant, dict):
                raise DeclarationUnreadable([
                    f"{RENUMBERS_REL}: renumbers[{subject!r}].prior_occupant is not an "
                    f"object -- it names the board the number belonged to and the range "
                    f"of row numbers that belongs to it"])
            # `floor` is OPTIONAL and defaults to the start of the ledger: a prior
            # occupant whose rows are a PREFIX of the subject's needs no floor, and
            # demanding one would make the common case noisier for no gain. What it is
            # NOT is inferrable -- #77's prior occupant sits at n=168..171 while the
            # reissued unit's own legs are n=30..31, so a ceiling alone puts those legs
            # on the wrong side of the boundary and reports the unit as having no intake.
            for key in ("board", "ceiling"):
                value = occupant.get(key)
                if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                    raise DeclarationUnreadable([
                        f"{RENUMBERS_REL}: renumbers[{subject!r}].prior_occupant.{key} must "
                        f"be a positive integer, got {value!r} -- a partition that does not "
                        f"resolve is indistinguishable from no partition, and the two "
                        f"readings differ by another unit's close"])
            floor_value = occupant.get("floor")
            if floor_value is not None:
                if (not isinstance(floor_value, int) or isinstance(floor_value, bool)
                        or floor_value <= 0):
                    raise DeclarationUnreadable([
                        f"{RENUMBERS_REL}: renumbers[{subject!r}].prior_occupant.floor must "
                        f"be a positive integer, got {floor_value!r}"])
                if floor_value > int(occupant["ceiling"]):
                    raise DeclarationUnreadable([
                        f"{RENUMBERS_REL}: renumbers[{subject!r}].prior_occupant.floor "
                        f"({floor_value}) is ABOVE its ceiling ({occupant['ceiling']}) -- an "
                        f"empty range excludes nothing while reading as a declared partition"])
                prior_floor = int(floor_value)
            prior_board = int(occupant["board"])
            prior_ceiling = int(occupant["ceiling"])
        out[str(subject)] = Renumber(
            str(entry["was"]), str(entry["evidence"]),
            prior_board, prior_floor, prior_ceiling)
    return out


def load_exemptions(repo: Path) -> list[tuple[str, str, str, str, str]]:
    """The declared sequence exemptions, or a typed failure naming the skeleton.

    WHY THIS IS A FILE AND NOT A CONSTANT IN THE TOOL, and the reason is law rather than
    taste. `SKILL.md` section 11: *"an exemption table holding the entries as FACTORY DATA
    in its own file and never inline in the gate, because the gate is paired byte-identically
    with its template copy and a factory sha must not ship to every new factory."* The
    ledger's `EXEMPTIONS` was the LAST surface still inline -- its three entries name
    meta-factory's own `#6`/`#8` closes of 2026-09-12 and their granting rulings -- while
    `docs/ledger-commit-exemptions.json`, `docs/ledger-no-shrink-exemptions.json` and
    `docs/ledger-retirements.json` already follow the rule. So a factory that happened to
    work on `#6` or `#8` would have had its OWN sequence defects excused by another
    factory's history, and the two entries say so in terms: *"close written before the gate
    existed"* is true of the template's ledger and of no other.

    ABSENT MEANS NONE, and MALFORMED IS A PROBLEM -- the convention the sibling surface
    states in its own words: *"Absent or empty data means no exemptions — the shipped state
    of a new factory. Anything malformed is a problem, never a silent pass: an exemption
    list that quietly fails to load is indistinguishable from no exemptions."* The law's
    concern is the SILENT failure, and a missing file is a factory that has nothing to
    excuse; a file that exists and cannot be read is the one that must never pass quietly.

    `proof` is NOT required here, deliberately. The doctrine in `tools/ledger.py` refuses a
    proofless entry at the point it would EXCUSE an omission -- *"refusing on the USED path
    rather than at load keeps a fresh factory's dead entries invisible"* -- so a dead entry
    is named where a reader is already looking, rather than failing the whole declaration.
    """
    path = repo / EXEMPTIONS_REL
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DeclarationUnreadable([f"{EXEMPTIONS_REL} exists but cannot be read: {exc}"]) from exc
    except json.JSONDecodeError as exc:
        raise DeclarationUnreadable([f"{EXEMPTIONS_REL} is not valid JSON: {exc}"]) from exc
    if not isinstance(payload, dict):
        raise DeclarationUnreadable([f"{EXEMPTIONS_REL} must be a JSON object carrying `exempt`"])
    rows = payload.get("exempt")
    if not isinstance(rows, list):
        raise DeclarationUnreadable([
            f"{EXEMPTIONS_REL} carries no `exempt` list — see {EXEMPTIONS_EXAMPLE_REL}"
        ])
    out: list[tuple[str, str, str, str, str]] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DeclarationUnreadable([f"{EXEMPTIONS_REL} exempt[{i}] is not an object"])
        # `proof` is carried through even when blank: the USE site refuses a proofless
        # entry by naming it, which is a better error than a whole-file rejection.
        missing = [k for k in ("subject", "leg", "granted", "reason") if not row.get(k)]
        if missing:
            raise DeclarationUnreadable([
                f"{EXEMPTIONS_REL} exempt[{i}] is missing {', '.join(missing)} — an "
                f"exemption names what it excuses and when it was granted"
            ])
        out.append((str(row["subject"]), str(row["leg"]), str(row["granted"]),
                    str(row["reason"]), str(row.get("proof") or "")))
    return out


def load_invariants(repo: Path) -> dict:
    """The declared `invariants` map, or `DeclarationUnavailable`/`DeclarationUnreadable`."""
    path = repo / DECLARATION_REL
    if not path.is_file():
        raise DeclarationUnavailable(
            f"no {DECLARATION_REL} — the boundary is a DECLARED factory parameter "
            f"(#78 clause b), and this tree has not declared one; the skeleton is "
            f"{DECLARATION_EXAMPLE_REL}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DeclarationUnreadable([f"{DECLARATION_REL} exists but cannot be read: {exc}"]) from exc
    except json.JSONDecodeError as exc:
        raise DeclarationUnreadable([f"{DECLARATION_REL} is not valid JSON: {exc}"]) from exc

    if not isinstance(payload, dict):
        raise DeclarationUnreadable([f"{DECLARATION_REL} must be a JSON object carrying `invariants`"])
    declared = payload.get("invariants")
    if not isinstance(declared, dict):
        raise DeclarationUnreadable([
            f"{DECLARATION_REL} carries no `invariants` map — see {DECLARATION_EXAMPLE_REL}"
        ])
    return declared

def boundary_for(repo: Path, key: str) -> tuple[dt.datetime, str]:
    """The boundary `key` names, as `(datetime, declared-text)`.

    The declared TEXT is returned beside the parsed value because the refusal and the
    `excused:` lines both quote the date the factory itself declared, rather than one
    this repository carries.
    """
    declared = load_invariants(repo)
    value = declared.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise DeclarationUnavailable(
            f"{DECLARATION_REL} declares no `{key}` boundary — this factory has not "
            f"adopted that invariant, so there is no boundary to judge against"
        )
    if not isinstance(value, str):
        raise DeclarationUnreadable([
            f"{DECLARATION_REL}: `{key}` must be an ISO-8601 UTC string, found "
            f"{type(value).__name__}"
        ])
    try:
        when = parse_ts(value)
    except (ValueError, TypeError) as exc:
        raise DeclarationUnreadable([
            f"{DECLARATION_REL}: `{key}` is not a readable ISO-8601 timestamp: "
            f"{value!r} ({exc})"
        ]) from exc
    return when, value
