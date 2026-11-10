# Commit hygiene — the close-keyword rule

**Owns:** how a commit message may reference an issue, and why. The mechanism lives in
`tools/hooks/commit-msg` with its predicate in `tests/test_commit_close_keyword.py`;
this file is the law a human reads, and the hook's header cites it (#89).

## The rule

Never put a GitHub closing keyword immediately before an issue reference:

```
close    closes    closed
fix      fixes     fixed
resolve  resolves  resolved
```

…followed by `#N`, optionally with a colon and one issue noun (`closes issue #10`,
`fixes bug #10`).

Put the reference **first** instead:

| Safe form | Example |
|---|---|
| reference first | `#142: edit deliveries get their own dimension` |
| refs form | `refs #142` |
| number-first ledger form | `ledger: #46 close row` |
| prose without adjacency | `board=closed (#40)` — measured control `411fab7`, closed nothing |

## Why — the parser is not a lane deciding anything

GitHub's closing-keyword parser reads the commit message **at push time**. The issue
closes the instant the push lands, before any lane has decided to close it. It reads the
**body** as well as the subject, so `this fixes #121` in prose closes #121 exactly as a
subject does.

And this clone pushes to **two** repos whose issue numbers are independent namespaces, so
one push can close two unrelated items. Measured: `f758758` closed canonical #46 and
mirror #46 five seconds apart, and the mirror's #46 was unrelated — it had to be reopened
by hand.

## The measured class

Measured 2026-11-10 at `HEAD` = `4bc91f0`, over the **1185 commits reachable from `HEAD`**,
subject and body scanned separately (`--all` reaches 1203 and includes topic branches that
never landed on `main`, so it is not the population this rule governs):

| Surface | Messages carrying keyword-then-ref |
|---|---|
| subject | **6** |
| body | **15** |
| total | **21** |

21 messages, 22 hits: `b2e252f3` carries two refs in one body (`Fix #1`, `Fix #2`). The
subject and body sets do not overlap. **A guard that read only subjects would have covered
6 of 21, 29 %.** The body is the larger half, and it is why the guard reads the whole
message.

### Which closes the parser actually caused

`closedAt` is the *latest* close, and a reopen erases the earlier instant. A parser close is
therefore provable only where the surviving gap between the commit and the close is
second-scale. Classifying all 21 messages against their targets' own `closedAt`:

| Verdict | Messages | Meaning |
|---|---|---|
| **record** | 7 | the board closed *before* the commit was authored: the message records a close, it does not cause one |
| could-be-cause | 13 | closed after authoring, but push time is unrecorded |
| reopened | 1 | `#46` is OPEN again, so its original instant no longer exists |

The two closes that reproduce as a parser's work, both **subject-borne**:

| Commit | Evidence |
|---|---|
| `420ff65` | #121 closed **+8 s** after the commit's own timestamp |
| `b22dd67` | #142 closed **+6 s** after the commit's own timestamp |

Nobody closes a board eight seconds after writing a commit message. The class is still
firing: the oldest instance is 2026-09-26, the newest 2026-11-09.

Two entries from the first draft of this section did not survive that check, and the reason
is worth keeping. `b7221d0`'s `#39` closed **56 days before** the commit existed, so it is a
record like the seven, not a cause. And `#50` / `#46` were reopened and re-closed, so the
`+3 s` / `+2 s` gaps were true when measured and are not reproducible from `closedAt` today:
a number measured against a mutable artifact has a shelf life.

**Body-borne parser closes: zero proven instances.** The three this section first cited
(`close #140`, `close #128`, `close #55`) are all records: every one closed before its commit
was authored, so a push cannot have caused it. The body surface is therefore guarded on
**preventive** grounds: GitHub documents closing keywords as working anywhere in a commit
message, subject or body, the two proven cases above show the parser acting at push time,
and 15 of 21 messages already carry a closing ref in the body. That is a documented
exposure, not an observed close, and the distinction belongs in the record.

## The mechanism, and its fail-open contract

`tools/hooks/commit-msg` is armed (`core.hooksPath = tools/hooks`). It loads **two**
predicates by path from `tests/` — `subject_cites_rows` (ledger row citations) and
`closes_issue` (this rule) — and refuses a message either one rejects. On a load failure
it prints a WARNING and lets the commit through: deliberate, so a fresh clone is never
blocked.

The consequence is part of the contract: **the gate file's path is part of the guard.**
Rename `tests/test_commit_close_keyword.py` and the refusal degrades silently to a
warning — a broken instrument reporting a clean zero. A contract test asserts that both
paths the hook names exist and define the predicate it loads, so a rename reds the suite
instead of quietly disarming the hook.

## Where the rule is enforced, and where it is only written

| Surface | Enforces? |
|---|---|
| `tools/hooks/commit-msg` | yes — refuses at commit time |
| `tests/test_commit_close_keyword.py` | yes — 9 refuse / 9 accept arms, body surface, bite proof; run by `pytest` **and** by `tools/audit.py` as a script |
| this file | no — it is the readable law, and the hook header cites it |

A rule with no mechanism is prose. #94 exists because this rule was prose for six weeks
while the class kept firing; #89 exists because the law and the guard have to land
together or neither counts.
