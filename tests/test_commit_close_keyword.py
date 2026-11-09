#!/usr/bin/env python3
"""Gate: a commit message must not carry a closing keyword before an issue ref.

Origin: #56, ruled in the ops router, and left a law without a mechanism until #94.
GitHub's closing-keyword parser reads the commit message at push time, so a subject
such as `fix: #142 …` closes the issue the moment it lands. This clone pushes to TWO
repos whose issue numbers are independent namespaces, so one push can close two
unrelated items — measured, `f758758` closed canonical #46 and mirror #46 five seconds
apart, and the mirror's #46 was an unrelated item that had to be reopened by hand.

The population is larger than #94 recorded. Measured 2026-11-09 over 1196 commits:

    b22dd67  fix: #142 edit deliveries get their own verdict-store dimension   (closed 20:16:26Z, +6s)
    b3754ec  fix: #122 ignore commands and messages from other bots in groups
    420ff65  fix: #121 reset the pool index on success so the declared lead is tried first
    b7221d0  ledger: close #39 on the demonstrated condition …
    f758758  ledger: close #46 — container-name fix verified live and held
    8fe7155  ledger: close #50 — dead transport retry layer removed (#50)

Six instances, not the two #94 knew about, and the class is still firing: #121 and #142
are four weeks and one day apart, both `fix: #N`, both closed by the parser rather than
by a lane that decided to close them.

Why the guard reads the WHOLE MESSAGE, not the subject
------------------------------------------------------
GitHub parses the message body too. A body sentence `this fixes #121` closes #121 exactly
as a subject does, and #94's dispatched close condition named the subject alone — a hook
built literally to it would refuse `ledger: close #46` and pass `fix: #142`, which is the
spelling that actually fired. Both surfaces are checked here.

Why number-first is safe, and why the guard is not a blanket refusal
--------------------------------------------------------------------
The parser needs keyword-then-reference ADJACENCY. `#142: edit deliveries …`,
`refs #142` and `ledger: #46 close row` all fire nothing — measured control: `411fab7`'s
`board=closed (#40)` closed nothing. The ACCEPT arms below are therefore not decoration:
without them the guard could be satisfied by refusing every commit, which is the failure
shape this repo calls a broken instrument reporting a clean zero.

Run:  python3 tests/test_commit_close_keyword.py
Exit: 0 the predicate rejects every closing spelling and accepts the safe ones.
"""

from __future__ import annotations

import re
import sys

# GitHub's documented closing keywords. `fix(e[sd])?` covers fix/fixes/fixed;
# `close[sd]?` covers close/closes/closed; `resolve[sd]?` covers resolve/resolves/resolved.
KEYWORDS = (
    "close", "closes", "closed",
    "fix", "fixes", "fixed",
    "resolve", "resolves", "resolved",
)

# Keyword, an optional colon (`Fixes: #10` is a documented form), then the reference.
# The reference may be separated by one issue noun — `closes issue #10`, `fixes bug #10`
# — because that spelling is close enough to adjacency to be worth refusing, and the
# ACCEPT arms prove the widening does not swallow ordinary prose.
_KEYWORD_THEN_REF = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+(?:issue[s]?|bug[s]?)?\s*#\d+\b",
    re.IGNORECASE,
)

# The bare adjacency form (`fix: #142`) needs no whitespace requirement.
_KEYWORD_ADJACENT = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#\d+\b",
    re.IGNORECASE,
)


def closes_issue(message: str) -> list[str]:
    """Every keyword-then-reference match in `message`, or [].

    Returns the MATCHES rather than a bool so a caller can name the offending text:
    a refusal that says only "refused" makes the author hunt for the trigger, and a
    message with three references has one that matters.
    """
    hits: list[str] = []
    for pattern in (_KEYWORD_ADJACENT, _KEYWORD_THEN_REF):
        hits.extend(m.group(0) for m in pattern.finditer(message))
    # Order-preserving de-duplication: the two patterns overlap on the adjacent form.
    seen: set[str] = set()
    unique: list[str] = []
    for hit in hits:
        key = hit.lower()
        if key not in seen:
            seen.add(key)
            unique.append(hit)
    return unique


# --- probes -----------------------------------------------------------------

# The two historical offenders, verbatim from `git log`. Named as the reason the rule
# exists; they are published and cannot be repaired, so they are evidence, not backlog.
REFUSE_CASES = [
    "fix: #142 edit deliveries get their own verdict-store dimension",
    "ledger: close #46 — container-name fix verified live and held",
    "ledger: close #50 — dead transport retry layer removed (#50)",
    "Fixes #142",
    "Fixes: #142",
    "closes issue #142",
    "resolve #142 in the next pass",
    "ledger: close #39 on the demonstrated condition",
    "fix: #122 ignore commands and messages from other bots in groups",
]

# Each of these must PASS. `411fab7`'s subject is the measured control.
ACCEPT_CASES = [
    "#142: edit deliveries get their own verdict-store dimension",
    "#142 edit deliveries get their own verdict-store dimension",
    "refs #142",
    "see #142",
    "feat(audit): gate that a close row records board=closed (#40)",
    "ledger: #46 close row",
    "ledger: rows for #58 (#58 intake, hq)",
    "closes the gap #142 opened",
    "#130 roster: is_registered partitions admin_ids from telegram_admin_ids",
]


def _probe_predicate() -> list[str]:
    """The two-arm probe: every refuse case fires, every accept case stays silent."""
    problems: list[str] = []
    for subject in REFUSE_CASES:
        if not closes_issue(subject):
            problems.append(f"predicate MISSED a closing spelling: {subject!r}")
    for subject in ACCEPT_CASES:
        if closes_issue(subject):
            problems.append(
                f"predicate FIRED on a lawful subject: {subject!r} -> {closes_issue(subject)}"
            )
    return problems


def _probe_body() -> list[str]:
    """The body is a closing surface too — #94's condition named only the subject."""
    message = (
        "b22dd67 fix: #142 edit deliveries get their own dimension\n"
        "\n"
        "This fixes #121 as well.\n"
    )
    hits = closes_issue(message)
    if not hits:
        return ["predicate MISSED a closing keyword in the message BODY"]
    return []


def _probe_bite() -> list[str]:
    """A weakened predicate must FAIL these probes, or the probes prove nothing.

    Removing `fix` from the keyword set is exactly the mistake #94's own close
    condition invited (it named the `ledger: close` spelling only), so this is the
    regression it guards against rather than a hypothetical.
    """
    weakened = re.compile(
        r"\b(?:close[sd]?|resolve[sd]?)\s*:?\s*#\d+\b", re.IGNORECASE
    )
    if weakened.search("fix: #142 edit deliveries"):
        return ["bite proof failed: the weakened predicate still caught `fix: #142`"]
    if not weakened.search("ledger: close #46 — container-name fix"):
        return ["bite proof failed: the weakened predicate lost the `close #46` arm"]
    return []


def main() -> int:
    problems = _probe_predicate() + _probe_body() + _probe_bite()
    if problems:
        print("close-keyword gate: FAILED")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        f"close-keyword gate: clean — {len(REFUSE_CASES)} closing spellings refused, "
        f"{len(ACCEPT_CASES)} lawful subjects accepted, body surface covered, "
        "bite proof intact"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
