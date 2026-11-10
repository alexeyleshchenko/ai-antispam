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

Measured 2026-11-10 over the **1181 commits reachable from `HEAD`**, subject and body
scanned separately (`--all` reaches 1199 and includes topic branches that never landed on
`main`, so it is not the population this rule governs):

| Surface | Messages carrying keyword-then-ref |
|---|---|
| subject | **6** |
| body | **15** |
| total | **21** |

The six **subject-borne** instances, with the effect each had on a board:

| Commit | Effect |
|---|---|
| `8fe7155` | #50 closed +3 s |
| `f758758` | #46 closed +2 s; mirror #46 +5 s (unrelated, reopened by hand) |
| `b7221d0` | #39 closed |
| `420ff65` | #121 closed +8 s |
| `b3754ec` | #122 |
| `b22dd67` | #142 closed +6 s |

Six and eight second gaps are a parser, not a decision. The class is still firing: the
oldest instance is 2026-09-26 and the newest is 2026-11-09.

**The body half is the larger half, and it is why the guard reads the whole message.** Of
the 15 body-borne instances, three are ledger rows where the board closed by parser rather
than by the lane (`close #140`, `close #128`, `close #55`); the rest are `Closes #42` (x3),
`Closes #40` (x3), `Closes #25`, `Closes #32`, `Closes #44`, `fixes #3`, `Fix #1`,
`Fixes #32` - intended closes, but executed by the parser at push time with no lane
deciding. A guard that read only subjects would have covered 6 of 21.

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
