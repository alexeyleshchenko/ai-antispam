# #145 — protected-channel leave guard: pre-state and verification

Written 2026-11-09, before the grace expiry. Close condition 4 is the live run at
`2026-11-16T11:12:45Z` and **cannot** be asserted here; everything else is.

## What the guard does

`leave_no_rights_groups` reads `get_protected_channel_ids()` once per run and, for
any group in that set, takes `pause_group_preserving_data` instead of
`perform_complete_group_cleanup`. It never calls `bot.leave_chat` for a protected
channel.

The pause is load-bearing, not belt-and-braces: both populations that feed this flow
filter `status = 'active'` (grace `group_operations.py:817`, sweep `:1841`), and
`set_no_rights_detected_at` writes only `WHERE no_rights_detected_at IS NULL`
(`:614`). Clearing the clock on an `active` row therefore re-stamps it on the next
sweep — a 7-day loop with a WARNING per sweep. The pause removes the row from both
populations and makes the hold terminal.

## Live pre-state (measured 2026-11-09, `psql` against `ai_spam_bot`)

| group_id | title | status | moderation_enabled | linked_channel_id | no_rights_detected_at |
|---|---|---|---|---|---|
| -1002107631154 | SV CLUB | active | t | -1002021099836 | 2026-11-09 11:12:45.356649+00 |
| -1002021099836 | SV CHAT | active | t | -1002107631154 | (null) |

Both members of the pair are mutually linked, so `get_protected_channel_ids()`
(`SELECT DISTINCT linked_channel_id WHERE NOT NULL`) returns **both** ids. The guard
therefore protects SV CHAT — the row carrying the funded admin and 58 approved
members — as well as the vestigial channel row.

SV CLUB's grace expires at **2026-11-16T11:12:45Z**, the earliest in the flagged
population. It is `unreadable` (both `getChatMember` and `getChatAdministrators`
return 400), not `administrator`-class, so it is not covered by #144's hold and would
have been left.

## Close conditions

| # | Condition | State |
|---|---|---|
| 1 | protected channel never passed to the leave path | met — `test_protected_channel_is_never_left` |
| 2 | row reads `paused`, clock NULL, second run does not re-enqueue | met — `test_protected_channel_clock_cleared_so_a_second_run_does_nothing` |
| 3 | a non-protected group still leaves | met — `test_non_protected_group_still_leaves_beside_a_protected_one` |
| 4 | live run on 2026-11-16 shows SV CLUB absent from the leave list and SV CHAT intact | **owed — the 16th** |
| 5 | mappings preserved after the pause | met — `test_pause_group_preserving_data_keeps_mappings` |

Condition 5 is a real-DB test seeded with both roster rows as they exist live: SV CLUB
carries the 67-credit admin, SV CHAT the mapping plus 58 approved members. It asserts
both survive, `bot_removed_at` NULL on both, SV CHAT still `active` with
`moderation_enabled` set, and exactly one `group_paused` event. Its final block runs
`cleanup_group_data` on the same rows as a **positive control** — the destructive
sibling provably zeroes both counts, so the assertions are capable of failing.

The guard is also structurally unable to reach the destructive path:
`cleanup_group_data` is no longer imported into `no_rights`, asserted as
`not hasattr(no_rights, "cleanup_group_data")`.

## What condition 4 must show

At or after `2026-11-16T11:12:45Z`, on whatever is deployed:

1. `SV CLUB` is **absent** from `get_groups_with_no_rights_past_grace(7)` — it is
   `paused`, so it left both populations.
2. `SV CLUB.status = 'paused'`, `no_rights_detected_at IS NULL`,
   `bot_removed_at IS NULL`.
3. `SV CHAT` unchanged: `status = 'active'`, `moderation_enabled = true`,
   `group_administrators` = 1 row, `approved_members` = 58.
4. `SV CLUB`'s own `group_administrators` row still present.
5. The WARNING names the channel and the discussion group, and appears **once** — a
   second occurrence seven days later means the pause did not land and the loop is
   back.

A restart after that instant runs the job immediately (`scheduled_tasks.py:126-137`
calls `run_scheduled_jobs()` on entry, then sleeps `SECONDS_PER_DAY`), so the
deadline is the instant, not the next tick.

## Note on a test that was deleted

A test asserting that a held row reactivates cleanly through `update_group_admins`
was written and then **deleted**: it passed with the branch's
`moderation_enabled = TRUE` removed, because the SQLite test adapter rewrites the
plain `ON CONFLICT ... DO UPDATE` to `INSERT OR REPLACE` (`tests/conftest.py:281`),
which deletes and re-inserts the row and restores every column the branch would have
set. The branch is fully masked there, so the test certified nothing. The invariant
rests on the source, not on that test.
