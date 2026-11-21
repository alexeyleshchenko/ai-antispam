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

### The second detector: the bot's own membership (#143)

`is_member_list_inaccessible_error` fires only when the member list cannot be
**read**. That is one way to lack rights, and until #143 it was the only one
anything looked for — so a group whose list reads perfectly while the bot holds
neither `can_delete_messages` nor `can_restrict_members` sat `status='active'`,
`moderation_enabled=true`, and passed every check the system had. Measured
2026-11-09: 3 of 43 moderated groups, one of them an `administrator` with both
rights false — which still receives every message, so it reads healthy on any
check that looks at status alone.

The sweep now asserts the bot's **own** membership once per group per sweep
(`probe_bot_moderation_rights`, one `get_chat_member(bot_id)` call) and drives
the same clock:

| Probe result | `no_rights_detected_at` | Bucket |
|---|---|---|
| administrator with both rights | cleared if set, untouched otherwise | `rights_cleared` / — |
| administrator missing either right | set on first detection | `rights_flagged` |
| not an administrator (`member`, `restricted`, …) | set on first detection | `rights_flagged` |
| the probe itself failed | **left exactly as it was** | `rights_unverified` |

Three properties are deliberate:

- **The probe runs after the roster sync, outside its `try`.** A probe failure
  must not cost the group its roster refresh, and it must not be classified as a
  lifecycle event — it is `unverified`, a number in the summary.
- **A failed probe is never health.** It neither sets nor clears the clock. "We
  could not ask" and "we asked and it is fine" are different answers, and
  conflating them is the defect class this closes.
- **Writes happen on a transition only.** The sweep selects
  `no_rights_detected_at` in the same query that selects the group, so 39 of 43
  healthy groups write nothing and a flagged group does not churn a fresh stamp
  every day.

The **clock** is unchanged: it routes into the same 7-day grace. The **action**
is class-dependent: #144 split it on the bot's membership, #150 split the
degraded-administrator class on whether any registered admin is still reachable,
and #161 amended that split again — on **positive evidence**, and with the leave
withdrawn. The table below is the authority; the paragraph that stood here
claimed the action was unchanged, which was true only before #144.

### The disposition table (#144, amended by #161)

`leave_no_rights_groups` reads the bot's membership when the grace expires, then
asks `has_human_admin(group_id)` and `count_registered_admins(group_id)`, and
routes on both.

| Bot membership at expiry | Human admin? | Disposition |
|---|---|---|
| `administrator`, both rights | — | clear the flag, stay (unchanged) |
| `administrator`, missing a right | **yes** | notify, keep the flag, **do not leave** (#144) |
| `administrator`, missing a right | **no, and no admin is on file at all** | **hold** — data gap, not the orphaned class: flag kept, clock keeps running, nothing written (#161) |
| `administrator`, missing a right | **no, and admins ARE on file** → class `orphaned` | `pause_group_preserving_data(reason="no_rights_orphaned_group")`, clock cleared, WARNING; **terminal: stay, silent, no retry** (#161) |
| `member` / `left` / `kicked` | — | leave after grace (unchanged) |
| unreadable / inaccessible | — | leave after grace (unchanged) |

**`has_human_admin` is the discriminator**, not "every admin is inactive": at
least one `group_administrators` row whose `administrators` row is active, who is
still a member, and who is not the bot itself. An empty roster is a data gap —
a sweep that never captured the list — and #150's error was reading it as proof
that every human had blocked us.

**The orphaned class never leaves.** HQ's ruling on #161 is *"stay, silent, no
retry"*: the group is dormant, so residency costs nothing measurable, while
eviction is irreversible and destroys the `approved_members` and
`group_administrators` rows that are the evidence of what happened. No DM is
sent (every registered admin is gone, so it would reach nobody) — but the group
stays in the **inventory** population and `scripts/bot_health_probe.py` prints
one line per orphaned group in the daily digest, so it cannot hide by being
silent.

**History:** #150 wrote this class as `no_rights_no_reachable_admin` and added a
leave when the bot was the group's sole member. Both are withdrawn. The old
reason string survives only on rows already written to `entity_events` (2 as of
2026-11-21), and the digest matches it too — otherwise those groups would drop
out of the inventory on a rename.

**Read `bot_removed_at`, never `status` alone, to tell a hold from a leave.** The
two "leave" rows above run `_leave_group_and_record_for_notification`, which calls
`perform_complete_group_cleanup(..., status="paused", reason="no_rights_past_grace")`
— so a group the bot has **left** also reads `status='paused'`, with
`bot_removed_at` set and both mapping tables at 0. `paused` on its own is therefore
not evidence of a hold. Measured 2026-11-20 on the 11-16 tick's output:

| Group | `status` | `bot_removed_at` | roster / approved | Class |
|---|---|---|---|---|
| `-1003078404548` | `paused` | `20:19:55.48Z` | 0 / 0 | leave |
| `-1002509060912` | `paused` | `20:19:56.45Z` | 0 / 0 | leave |
| `-1003751652625` | `paused` | NULL | **1 / 1** | #150 terminal pause |

The third row is also the **positive control** for #150's non-destructiveness: every
registered admin (`7054850510`) is `is_active=false`, so the terminal branch fired,
and both mapping tables survived — which is what #151's fix is meant to guarantee
and what the sole-member branch destroyed before it.

**Why the third row exists (#150, ruled 2026-11-16).** #144's hold is licensed by
the notify: the DM asks a human to tick one checkbox. `notify_degraded_admin_rights`
skips every admin it cannot reach (`no_rights.py:95-97`, `if admin and not
admin.is_active: continue`), so when *every* admin is inactive the notify sends
nothing and returns `sent = 0` — the hold has no terminating condition and the
group is held forever in silence. That is the same silent-degradation shape #144
moved the notify to detection time to avoid.

**Pause, not leave.** The terminal branch is a **status write**:
`pause_group_preserving_data` (`group_operations.py:340`), already built for
#145's protected-channel branch and documented as *"right when the bot is
STAYING but cannot act"*. `perform_complete_group_cleanup` hard-deletes
`group_administrators` and `approved_members` and a re-add restores neither, so
it is never the disposition here. Terminal by construction: both populations
filter `status = 'active'`, so the pause removes the row from
`get_groups_with_no_rights_past_grace` and `sweep_group_administrators` at once —
the one-shot property #145 relies on.

**The sole-member leave, and what it does NOT license.** Leaving the chat is
correct once no human remains, because there is nobody for the preserved mappings
to serve. It is **not** a licence to delete them: #150's ruling of 2026-11-16T21:07Z
reaffirmed condition 7 (`paused` … **mappings intact**) after a live run showed the
bot's own leave event driving `status_handlers.py:547/:575` into
`perform_complete_group_cleanup`, which overwrote the `paused` written 0.76 s
earlier and hard-deleted both mapping tables. That is #151 (closed
2026-11-16T22:15:42Z): the self-leave path must not run destructive cleanup for a
group already paused by this branch.

**Fail safe.** An unreadable member count, or a count read that raises, means
pause-only, no leave. Leaving is the irreversible direction, so it never runs on
an uncertain probe. `rights_unverified` is not zero.

**#145's guard wins.** A group in `get_protected_channel_ids()` never reaches this
branch — the protected-channel `continue` precedes it.

**One correction, measured 2026-11-20.** A peer probe reported
`-1002496127723` блатные чупани😎🐾 as *still* an administrator with
`member count = 1` and **2 `approved_members` / 1 `group_administrators`** rows,
i.e. as a group still being held. That state is two days stale. Read live: the
bot's own `getChatMember` returns **403 "bot is not a member of the supergroup
chat"**; the row is `status='left'` with `bot_removed_at=2026-11-16T21:01:08Z`;
and both mapping tables are **0** — destroyed at 21:01:08Z, seventy-four minutes
before #151 landed. So this group is not an open instance of the hold; it is the
measured instance of the #151 defect, already terminal. Its 96 credits sit on
admin `5272438221`, `is_active=false`, last active **2026-05-15**.

`reconcile_bot_rights` writes no
`status` and no `moderation_enabled` — #141's ruling rejected every disposition
that would have added a second writer of `status`.

Operator visibility: one `ERROR` line at **first detection**, naming the group
and which right is missing (the Telegram log handler sits at `WARNING`, so the
`INFO` line the older path emitted reached nobody), plus a daily report-only
count from `check_moderated_groups_without_bot_rights` — the inverse of the #41
monitor, reading the clock the sweep sets and repairing nothing.

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

The two moderation monitors run **after** the sweep, and must: the #143 leg
reports the clock the sweep is what sets, so running it first would describe
yesterday's state. Both are reports — neither repairs.

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
