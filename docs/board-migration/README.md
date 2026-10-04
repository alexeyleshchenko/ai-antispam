# Board rescue snapshot — `alexeyleshchenko/ai-antispam`

**Taken:** 2026-10-04T20:28Z by the HQ lane, before any migration of the service board.
**Why:** the owner ordered the issue board moved to `leshchenko1979` (*"alexeyleshchenko is a
deleted account"*). This token has **read-only** access to the source repo
(`permissions.push = false`, `admin = false`), so GitHub's `TransferIssue` is refused —
`leshchenko1979 does not have the correct permissions to execute TransferIssue`. A lossless
transfer is therefore **not possible from this lane**. This snapshot preserves every issue and
comment so nothing is lost if the account is deleted before the migration lands.

## Contents

| file | what |
|---|---|
| `board-export.json` | all 62 issues: number, title, body, state, stateReason, createdAt, closedAt, author, labels, url, and every comment (author, createdAt, body) |
| `number-map.json` | the old→new map written by the migration (`tools/board_migrate.py`) — **never hand-edit** |

## Census at snapshot time

| quantity | value |
|---|---|
| issues | 62 (21 OPEN, 41 CLOSED) |
| numbers | 3–77 (gaps where items were never filed) |
| comments | 148 across 49 issues |
| labels | 0 (the board carries no labels) |
| authors | `leshchenko1979` 54 · `alexeyleshchenko` 8 |

## Number-space collision — why this is not a plain copy

`leshchenko1979/ai-antispam` already occupies issue-space numbers **1–48** (18 issues + 29 pull
requests share one counter), so the next number assigned there is **49**. Any issue created on
it starts at 49 and cannot keep a number ≤ 48.

- Board **#49–#77** (29 items) can keep their numbers if created first.
- Board **#3–#48** (33 items) would land at **#78–#110**.

That matters because `evidence/ledger.jsonl` cites 39 distinct `#N` in row subjects: 29 of them
are ≥49 (preservable) and 10 are ≤48 (#35, #39, #41–#48, which would shift).

## Status

**Migration COMPLETE 2026-10-04.** Mechanism: recreate-and-renumber, via `tools/board_migrate.py`
(verbs `plan` / `run` / `verify`). All **62 issues recreated on `leshchenko1979/ai-antispam` as
#49–#110**, all **148 comments reposted**, all **41 closed items re-closed**, each issue carrying a
provenance header and each comment a `migrated from` prefix. `python3 tools/board_migrate.py verify`
→ `62 mapped, 0 discrepancies`. Canonical census after the move: **80 total / 27 open / 53 closed**
(18 pre-existing + 62 migrated). The old→new map is `number-map.json`.
