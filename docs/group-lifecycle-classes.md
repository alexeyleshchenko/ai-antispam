# Group lifecycle: classify the failure, then act on it

The sweep used to count failures. `synced: 44, failed: 10` was the whole story,
so ten groups that Telegram had permanently lost were re-probed at every start
and every sweep, wrote the same warning each time, and stayed `status='active'`
forever. A real failure arriving in that noise was indistinguishable from the
standing ten.

The rule now: **a per-group failure is a lifecycle observation, not a bare
count.** Each one is classified into a class with a defined action, and the
class that is not recognised stays loud.

## The classes

`act()` in `src/app/database/group_operations.py` classifies in this order. The
order is load-bearing, not stylistic.

| Class | Predicate | Action | Bucket |
|---|---|---|---|
| `migrated` | `migrate_target_id(exc)` — a `TelegramMigrateToChat` carrying `migrate_to_chat_id` | `migrate_group(old, new)`: create the destination row, move the roster, retire the old id with `reason='migrated'` | `migrated` |
| `no_rights` | `is_member_list_inaccessible_error(exc)` — `TelegramBadRequest` with `member list is inaccessible` | `set_no_rights_detected_at`: start the existing 7-day grace, notify the admin, leave nothing retired | `no_rights` |
| `gone` | `is_group_inaccessible_error(exc)` — chat not found, kicked, group deleted | `retire_group(reason='sweep_inaccessible')` | `retired` |
| unclaimed | anything else | nothing; one `skipped — <Type>: <msg>` WARNING | `failed` |

**`migrated` is consulted first** because `TelegramMigrateToChat` is not a
`TelegramBadRequest`: it subclasses `TelegramAPIError`. A predicate written the
other way round would let a migration fall through to `failed`, and the group
would keep being probed at an id that no longer exists while the real supergroup
went unmoderated.

**`no_rights` is consulted before `gone`** because a `TelegramBadRequest` can
carry either marker, and a rights problem is recoverable while a missing chat is
not. Retiring on a rights error would silently cancel a paying customer's
protection: `-1001156881276` alone carries 7 registered seats.

## The reason vocabulary is two strings

`entity_events.reason` takes `sweep_inaccessible` (the sweep retired it) and
`migrated` (it moved). Both are written by `_retire_group`, one event per
transition, with the pre-transition row as `old_row`.

`record_bot_removed` writes **no** `entity_events` row: it stamps
`bot_removed_at` and sets `status='left'` in one UPDATE. The removal event is
already the observation; a second row would duplicate it.

## Decisions this encodes

- **A rights error never retires a group.** It routes into the grace flow that
  already existed (`no_rights_detected_at` → 7 days → re-verify → notify →
  leave). The sweep does not get a second, silent retirement path.
- **Retirement keeps the roster.** `retire_group` flips status and stamps the
  instant; it never deletes `group_administrators` or `approved_members`.
  `cleanup_group_data` is for confirmed deletions, not for observations.
- **Migration copies identity and roster only.** `member_joins` is **not**
  moved, so trust at the new id starts closed: a migration is a data copy, not
  fresh observation.
- **`bot_added_at` is copied verbatim.** Leaving it NULL fails
  `is_pre_existing_candidate` at its first leg, which would permanently deny the
  benefit of the doubt to everyone at the new id. `no_rights_detected_at` is
  deliberately not copied.
- **`bot_removed_at` means "removed at", and a re-add clears it.** The 72-hour
  pre-existing window restarts on reactivation, because while the bot was out it
  was not watching, so "I never saw you join" proves nothing about that stretch.
- **Dormant is a counter, not an action.** `DORMANT_AFTER_DAYS` is 90, and it is
  counted only for groups that synced in this run: a group that failed tells you
  nothing about its activity.

## Job order

`leave_no_rights_groups()` runs before the sweep (`scheduled_tasks.py:64` then
`:99`). A group whose grace expired is left, and the sweep then sees the
consequence rather than racing it.

## Rows

Every row was reverted individually at the final tree, the named test re-run,
and the source restored. Restores are verified by `md5sum`, not by memory.

| # | Behaviour | Test | Revert applied | Result |
|---|---|---|---|---|
| 1 | A `chat not found` group is retired, not merely counted | `tests/background_jobs/test_scheduled_jobs.py::test_sweep_retires_a_chat_not_found_group` | classifier removed: every failure falls through to `failed` | RED |
| 2 | A `TelegramMigrateToChat` creates the destination and retires the old id | `…::test_sweep_migrates_an_upgraded_group` | same classifier removal | RED |
| 3 | `member list is inaccessible` flags no-rights and does **not** retire | `…::test_sweep_flags_member_list_inaccessible_without_retiring` | fold the marker into the gone-markers **and** invert the rights/gone branch order | RED |
| 4 | A kicked group is retired | `…::test_sweep_retires_a_kicked_group` | classifier removal | RED |
| 5 | The second sweep run is quiet: no re-retirement, no second event | `…::test_sweep_second_run_is_quiet` | classifier removal | RED |
| 6 | Dormant is counted only for groups that synced | `…::test_sweep_counts_dormant_only_for_synced_groups` | count dormant for every fetched row | RED |
| 7 | `retire_group` is idempotent and emits one event | `tests/database/test_group_operations.py::test_retire_group_is_idempotent_and_emits_one_event` | drop `AND status <> $4` from the UPDATE guard | RED |
| 8 | `retire_group` refuses a missing group | `…::test_retire_group_refuses_a_missing_group` | treat a `None` snapshot as an empty row and proceed | RED |
| 9 | `retire_group` keeps the roster | `…::test_retire_group_keeps_the_roster` | delegate to `cleanup_group_data` | RED |
| 10 | `bot_removed_at` is stamped once and never moved | `…::test_retire_group_is_idempotent_and_emits_one_event` | replace `COALESCE(bot_removed_at, NOW())` with `NOW()` | RED |
| 11 | The migration copies `bot_added_at` verbatim | `…::test_migrate_group_moves_roster_and_retires_the_old_row` | copy `NOW()` instead | RED |
| 12 | The migration never clobbers an existing destination row | `…::test_migrate_group_does_not_clobber_an_existing_destination_row` | `ON CONFLICT (group_id) DO UPDATE` | RED |
| 13 | `migrate_target_id` reads the upgrade and nothing else | `tests/common/test_telegram_errors.py::test_migrate_target_id_reads_the_upgrade_and_nothing_else` | guard on `TelegramBadRequest` instead of `TelegramMigrateToChat` | RED |
| 14 | A rights error is not a gone error | `…::test_member_list_inaccessible_is_not_group_inaccessible` | fold the marker into `_INACCESSIBLE_MESSAGE_MARKERS` | RED |
| 15 | `record_bot_removed` retires the row | `tests/database/test_trust_operations.py::test_record_bot_removed_retires_the_row` | stamp `bot_removed_at` only, leave status | RED |
| 16 | A re-add clears `bot_removed_at` | `tests/database/test_lifecycle_status.py::test_update_group_admins_reactivates_paused_group` | drop `bot_removed_at = NULL` from the reactivation UPDATE | RED |
| 17 | The migration column is added at boot | `tests/database/test_schema_migrations.py` | remove the `GROUPS_MIGRATED_TO_DDL` await | RED |

## Rows that share one revert, deliberately

Rows 1, 2, 4 and 5 are four outcomes of one classifier, so removing the
classifier turns all four RED. That is the property: the classes are only worth
having if every one of them is reachable.

Rows 7 and 10 share the idempotency test. Both guards — the status predicate and
the `COALESCE` — protect the same promise, so either revert breaks it.

## Two rows that the plan expected to flip and do not

Both were measured, not assumed, and both are recorded because a matrix that
quietly drops a prediction teaches the next reader nothing.

**Row 3 needs two reverts, not one.** Folding `member list is inaccessible` into
the gone-markers leaves the sweep test green, because `act()` consults
`is_member_list_inaccessible_error` before `is_group_inaccessible_error`. The
branch order is a second, independent guard. Only the unit test on the predicate
flips under the marker change alone.

**Row 7's revert does not flip `test_sweep_second_run_is_quiet`.** The sweep
fetch filters `WHERE g.status = 'active'`, so a retired group is never fetched a
second time and never reaches the idempotency guard at all. The guard protects a
direct caller, which is why the database test is the one that proves it.

## Command

```bash
USE_SQLITE_TESTS=true uv run pytest \
  tests/background_jobs/test_scheduled_jobs.py \
  tests/database/test_group_operations.py \
  tests/database/test_trust_operations.py \
  tests/database/test_lifecycle_status.py \
  tests/database/test_schema_migrations.py \
  tests/common/test_telegram_errors.py -q
```

Full suite at the tip: **1246 passed, 4 skipped, 1 deselected**.
`ruff check src`: clean. `ty check src`: clean.

## What the first sweep should report

Stated in advance so a mismatch is a finding rather than a surprise. Run 1:
`total 54, synced 44, failed 0, retired 6, migrated 1, no_rights 3`. Run 2:
`total 48, retired 0, migrated 0`, and the standing ten warnings are gone.
