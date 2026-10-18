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
| numbers | 3–77 (62 items; 13 numbers in range never filed: 4, 7, 8, 9, 12, 13, 14, 16, 18, 20, 21, 22, 23) |
| comments | 148 across 49 issues |
| labels | 0 (the board carries no labels) |
| authors | `leshchenko1979` 54 · `alexeyleshchenko` 8 |

## Number-space collision — why this is not a plain copy

`leshchenko1979/ai-antispam` already occupies the issue counter **up to 48** (18 issues + 29 pull
requests, 47 of the 48 slots; #17 was never used), so the highest number in use is 48 and the next
one assigned there is **49**. Any issue created on it starts at 49 and cannot keep a number ≤ 48.

Recreating in ascending old-number order makes the map **`new = 49 + index`** with no exception —
**0 of the 62 issues kept its number**, and the two halves invert:

- Board **#3–#48** (33 items) → **#49–#81**.
- Board **#49–#77** (29 items) → **#82–#110** — they shift *up*, they do not stay put.

That matters because `evidence/ledger.jsonl` cites **53** distinct `#N` in row subjects: **42**
are ≥49 and **11** are ≤48 (#35, #36, #39, #41, #42, #43, #44, #45, #46, #47, #48 — pre-migration
numbers that must be resolved through `number-map.json`). The ≥49 half is **not** automatically
canonical: **29 of those 42 distinct numbers fall in #49–#77**, the band where the two namespaces
overlap, so a subject's namespace is resolved by its `ts` against the migration instant
(2026-10-04T20:43), never by the number alone. Measured in that band: **95** rows, **91** written
before the migration (old namespace) and **4** after (canonical #55, #65).

## Status

**Migration COMPLETE 2026-10-04.** Mechanism: recreate-and-renumber, via `tools/board_migrate.py`
(verbs `plan` / `run` / `verify`). All **62 issues recreated on `leshchenko1979/ai-antispam` as
#49–#110**, all **148 comments reposted**, all **41 closed items re-closed**, each issue carrying a
provenance header and each comment a `migrated from` prefix. `python3 tools/board_migrate.py verify`
reported `62 mapped, 0 discrepancies` **at the migration instant**; re-run on 2026-10-18 it reports
`62 mapped, 13 discrepancies` (**6** STATE, **7** COMMENTS) — every one of them a migrated item that
has since been worked (closed, or commented on), i.e. expected drift on a live board, not migration
loss. Read the map (`62 mapped`) as the migration's verdict and the discrepancy count as the
board's age. Canonical census at the migration instant (2026-10-04T20:43): **80
total / 27 open / 53 closed** (18 pre-existing + 62 migrated). That is a dated figure, not a live
count — the board has grown since; read `gh issue list` for the current one. The old→new map is
`number-map.json`.
