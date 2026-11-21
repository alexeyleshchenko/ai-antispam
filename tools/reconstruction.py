"""The reconstruction predicate, shared by the gate and by `verify`'s printer.

**Owns:** the reconstruction vocabulary and the interval computation — which rows are
reconstructions, whether a row names its basis, which close row answered a claim, and the
interval recomputed from the two rows' own `ts` values. A reconstruction has TWO arms, one
per event: a reconstructed `claim` and a reconstructed `intake`, both first-class members of
the one population this module reports (#111 option A, HQ ruling 2026-11-06T00:37:14Z).

WHY THIS MODULE EXISTS. `n=687` clause 1 moved the interval from DECLARED to
RECOMPUTED-AND-PRINTED, which puts the SAME recomputation in two places: the gate
(`tests/test_reconstructed_claim_declared.py` in the kit; a PORT OWED in this tree, declared
in `registry/gate-deferrals.json`, so the discriminating legs live in `tests/test_ledger.py`
until it lands) judges the self-declaration and prints the interval, and
`tools/ledger.py verify` prints it beside its `excused:` lines. Two private
copies of one predicate is this factory's own ruled class (`n=405` clause 5, `n=599`): they
drift on exactly the inputs that matter, and the drift is invisible because each copy is
self-consistent. So the predicate lives HERE and both call sites import it, on the same
bare-neighbour import `tools/ledger.py` already uses for `ledger_declaration` and
`field_predicate`.

WHAT THIS MODULE DOES NOT OWN. The BOUNDARY (which rows a ruling governs) is
`ledger_declaration.boundary_for`, and the declaration's READER is `tests/ledger_boundary.py`.
The verdict — is a missing basis a problem, is an empty population a skip — belongs to the
caller. This module computes and reports; it never judges.
"""

from __future__ import annotations

from ledger_declaration import parse_ts
from field_predicate import declares_token

# The token pair that puts a row in the population. Read LEXICALLY anywhere in the detail,
# scoped by the caller to the row's own event — a row of another event that merely QUOTES it
# is out of the population before the predicate is asked (`n=602`).
RECONSTRUCTION_KEY = "claim"
RECONSTRUCTION_VALUE = "reconstructed"

# THE INTAKE ARM (#111 option A, HQ ruling 2026-11-06T00:37:14Z). The 2026-10-04 board
# migration renumbered every service unit, so a pre-migration `claim`/`intake` keeps its OLD
# number and the canonical-number leg is rebuilt by a reconstructed intake declaring
# `intake=reconstructed` plus a `BASIS:` line. That token was written by the recipe and read
# by NOTHING: `RECONSTRUCTION_KEY` scopes the population to the event name `claim`, so a
# reconstructed intake was invisible and a basis-less one was indistinguishable from one that
# named its basis. The same VALUE, a different event and a different key — both scopes are
# required per arm, and the value is shared so the two arms cannot drift apart on it.
INTAKE_RECONSTRUCTION_KEY = "intake"

# The key each event's arm reads. Named once so `reconstructed_rows`, `reconstructed_claims`
# and `reconstructed_intakes` cannot disagree about which token belongs to which event.
RECONSTRUCTION_KEYS: dict[str, str] = {
    "claim": RECONSTRUCTION_KEY,
    "intake": INTAKE_RECONSTRUCTION_KEY,
}

# The canonical marker a reconstruction names its basis under. A colon-bearing marker, not a
# `=`-bearing trailer token, and that is load-bearing: `trailer_tokens` collects only tokens
# carrying `=`, so `BASIS:` can never be part of the canonical run and can never be confused
# with a declared measurement. WHAT the basis says is prose, and prose is not this module's
# to parse — the marker's presence is all that is asked.
BASIS_MARKER = "BASIS:"


def _declares_reconstruction(row: dict) -> bool:
    """True when this row is a reconstruction OF ITS OWN EVENT — the one predicate.

    The population is DOUBLY scoped, per arm: the EVENT scope keeps a ruling row that merely
    defines a token from reading as a reconstruction (`n=602`), and the TOKEN scope keeps an
    ordinary row from reading as one. Both are required — either alone finds the wrong rows.
    The key is looked up FROM the event, so a `claim` row that quotes `intake=reconstructed`
    is not a reconstructed intake and a `ruling` row that quotes either is neither.
    """
    key = RECONSTRUCTION_KEYS.get(row.get("event"))
    if key is None:
        return False
    return declares_token(row.get("detail", ""), key, RECONSTRUCTION_VALUE)

def reconstructed_claims(rows: list[dict]) -> list[dict]:
    """Every event `claim` row that declares itself a reconstruction.

    The claim arm's behaviour is unchanged by the intake arm's arrival: same event, same
    key, same value, same lexical read — the shared predicate below is the same two tests.
    """
    return [
        row
        for row in rows
        if row.get("event") == "claim" and _declares_reconstruction(row)
    ]

def reconstructed_intakes(rows: list[dict]) -> list[dict]:
    """Every event `intake` row that declares itself a reconstruction.

    The token to read is `intake=reconstructed`, and it is read VERBATIM like every other
    word-valued token here: `intake=reconstructed.` is prose, not a declaration. Measured on
    the live ledger 2026-11-21: four rows carry the token as a declaration (n=178, 187, 230,
    234), all with a `BASIS:` line. Two intakes are reconstructed and NOT in this population,
    and both are WRITE defects on their own rows rather than read defects here: `n=299`
    writes `intake=reconstructed.` with a full stop, so the value it declares is
    `reconstructed.` and the verbatim read refuses it exactly as it refuses any punctuated
    word; `n=316` calls itself a "RECONSTRUCTED INTAKE" in prose and carries no token at all.
    Both are named here rather than left to be rediscovered, because a population of four
    invites the question "why not five", and the honest answer is a property of those two
    rows, not a hole in this predicate. Neither is fixed from here: rewriting a written row
    is a ledger write, and this module only reads.
    """
    return [
        row
        for row in rows
        if row.get("event") == "intake" and _declares_reconstruction(row)
    ]

def reconstructed_rows(rows: list[dict]) -> list[dict]:
    """BOTH arms, in ROW ORDER — the population, as one list.

    One population rather than two, because the question both call sites ask is "which rows
    declare themselves reconstructions", and a reader that had to union two lists at each
    site would eventually union only one of them — which is the defect (#111) this arm
    exists to close.
    """
    return [row for row in rows if _declares_reconstruction(row)]


def names_basis(detail: str) -> bool:
    """True when this detail names the basis it rests on under the canonical marker."""
    return BASIS_MARKER in str(detail)


def intake_line(row: dict) -> str:
    """The line a caller PRINTS for a reconstructed intake — basis declared or MISSING.

    `verify` prints one of these per reconstructed intake, and the gate judges the same
    predicate: `names_basis` is REUSED, never re-implemented, so the printer and the gate
    cannot disagree about what "names its basis" means (`n=405` clause 5, `n=599`).

    A MISSING basis is stated as such rather than omitted from the line, because the whole
    defect class (#218, `n=111` option A) is a declaration written and never read: a reader
    who saw only `n=178 (subject #108)` could not tell a basis-less row from a row whose
    basis the printer chose not to show.
    """
    number = row.get("n")
    subject = row.get("subject")
    if names_basis(row.get("detail") or ""):
        return f"reconstructed intake: n={number} (subject {subject}) basis=declared"
    return (
        f"reconstructed intake: n={number} (subject {subject}) basis=MISSING — it declares "
        f"`{INTAKE_RECONSTRUCTION_KEY}={RECONSTRUCTION_VALUE}` and names no `{BASIS_MARKER}` "
        f"line, so nothing says what it reconstructs from"
    )

def population_line(rows: list[dict]) -> str:
    """The population this instrument EXAMINED, as the line both call sites print.

    Rule 7: a zero is a verdict only from a working instrument. Printed at zero too, and
    split by arm, so "no reconstructions" can never be confused with "my pattern did not
    match the text the tool emits" — the same reason `verify` prints `refs examined:` and
    `dispatch rows examined:` beside its verdict rather than implying them.
    """
    claims = reconstructed_claims(rows)
    intakes = reconstructed_intakes(rows)
    return (
        f"reconstructed rows examined: {len(claims) + len(intakes)} "
        f"({len(claims)} claim, {len(intakes)} intake)"
    )

def close_row_for(claim_row: dict, rows: list[dict]) -> dict | None:
    """The close row of this claim's WORK UNIT, or None when there is none.

    Resolved by SUBJECT, never by adjacency: a row's actor is read from its own identity
    fields, and row order is not an identity field. The row number is the tie-break, and it
    is the row's OWN identity rather than a fact about its neighbour — among the close rows
    of this subject, the earliest at or after the claim row is the one that answered it.
    """
    subject = claim_row.get("subject")
    number = claim_row.get("n")
    if not isinstance(number, int):
        return None
    candidates = [
        row
        for row in rows
        if row.get("event") == "close"
        and row.get("subject") == subject
        and isinstance(row.get("n"), int)
        and row["n"] >= number
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: row["n"])


def interval_line(claim_row: dict, rows: list[dict]) -> str:
    """The recomputed interval for this claim, as the line a caller PRINTS.

    The interval is RECOMPUTED from the two rows' own `ts` values and printed, never
    declared and never judged. Both sides are tool-sourced — the claim's own `ts` and its
    close row's — and no expected number is typed anywhere in this function.

    A work unit with no close row yet is NOT a defect: a lane reconstructing its claim
    mid-work has not closed yet, and this line says so rather than failing the row.
    """
    number = claim_row.get("n")
    subject = claim_row.get("subject")
    close = close_row_for(claim_row, rows)
    if close is None:
        return (
            f"reconstructed claim: n={number} (subject {subject}) interval=no close row yet"
        )
    try:
        recomputed = int((parse_ts(close["ts"]) - parse_ts(claim_row["ts"])).total_seconds())
    except (ValueError, TypeError):
        return (
            f"reconstructed claim: n={number} (subject {subject}) interval=uncomputable "
            f"(an unparseable ts on n={number} or n={close.get('n')})"
        )
    return (
        f"reconstructed claim: n={number} (subject {subject}) interval={recomputed}s "
        f"(claim ts {claim_row.get('ts')} -> close n={close.get('n')} ts {close.get('ts')})"
    )
