# #130 — behaviour matrix: one row, one test, one revert that proves it bites

The leg's rule is that a behaviour is not done until a test asserts it **and**
that test is seen to fail when the behaviour is reverted. A green test that
would pass on the broken code is a comment with a pytest header.

Every row below was reverted individually at the final tree, the named test
re-run, and the source restored. The revert column is what was actually
changed, not a description of the idea.

## Rows

| # | Behaviour | Test | Revert applied | Result |
|---|---|---|---|---|
| 1 | A discovered Telegram admin is visible to the moderation skip but is **not** a billing contact | `tests/database/test_group_operations.py::test_upsert_group_admin_never_promotes_a_roster_row_to_billing` | seed `is_registered = TRUE` in `upsert_group_admin`'s insert | RED |
| 2 | The skip reads the roster (`telegram_admin_ids`), not the billing list | `tests/handlers/message/test_validation_pre_existing.py::test_roster_only_admin_is_skipped_without_any_further_work` | `validation.py` reads `group.admin_ids` again | RED |
| 3 | A demoted payer is moderated like anyone else | `tests/handlers/message/test_validation_pre_existing.py::test_demoted_payer_is_not_skipped` | skip keyed on `admin_ids` (same revert as row 2) | RED |
| 4 | A roster write never emits `ON CONFLICT DO UPDATE` (the SQLite adapter would reset `is_registered`) | `tests/database/test_group_operations.py::test_roster_primitives_never_emit_do_update` | rewrite the insert as `ON CONFLICT ... DO UPDATE` | RED |
| 5 | `mark_admins_not_admin` refuses an empty keep-list (`NOT IN ()` is true for every row) | `tests/database/test_group_operations.py::test_mark_admins_not_admin_refuses_an_empty_keep_list` | drop the guard | RED |
| 6 | `get_group` costs a constant number of queries | `tests/database/test_group_operations.py::test_get_group_costs_the_same_number_of_queries` | split the partition into a second query | RED |
| 7 | `initialize_new_admin` grants once, keyed on `activated_at` | `tests/database/test_admin_operations.py::test_initialize_new_admin_second_start_is_noop` | key on row existence (`SELECT EXISTS`) | RED |
| 8 | A prefilled roster row still gets the welcome and the credits | `tests/database/test_admin_operations.py::test_initialize_new_admin_grants_prefilled_roster_row` | same revert as row 7 | RED |
| 9 | The sync is a dry run unless `apply=True` — including the **default argument** | `tests/test_sync_group_admins.py::test_the_default_argument_is_the_dry_run` | flip the signature default to `apply=True` | RED |
| 10 | A second `--apply` reports zero new seats | `tests/test_sync_group_admins.py::test_second_apply_reports_zero_new` | make the insert unconditional | RED |
| 11 | Bots and the anonymous-admin id are filtered out of the roster | `tests/test_sync_group_admins.py::test_bots_and_anonymous_admin_are_filtered` | drop the `is_bot` / `GROUP_ANONYMOUS_BOT_ID` filter | RED |
| 12 | A `chat_member` promotion writes a seat | `tests/handlers/test_chat_member_joins.py::TestChatMemberRosterWrites::test_promotion_writes_a_roster_seat` | disable the promotion branch | RED |
| 13 | A demotion flips the flag and keeps the row (a paying admin stays billable) | `…::test_demotion_flips_the_flag_and_keeps_the_row` | delete the row instead of flipping the flag | RED |
| 14 | A demotion of an unknown user creates no row | `…::test_demotion_of_an_unknown_user_creates_no_row` | route demotion through `upsert_group_admin` | RED |
| 15 | The join path is unchanged by the roster work | `tests/handlers/test_chat_member_joins.py::TestChatMemberJoins` | remove `record_join` | RED |
| 16 | The bot-add event syncs the roster, and a sync failure does not fail the add | `tests/handlers/test_status_handlers.py::TestRosterSyncOnBotAdd` | remove the sync call / remove the try/except | RED |
| 17 | The sweep isolates one failing group | `tests/background_jobs/test_scheduled_jobs.py::test_sweep_counts_a_failing_group_and_keeps_sweeping` | remove the per-group try/except | RED |
| 18 | A refused ban is one INFO line with no traceback, and writes the seat | `tests/handlers/test_spam_handlers.py::TestBanRefusalIsInfoNotTraceback::test_refused_ban_logs_info_without_traceback` | disable the target-admin branch | RED |
| 19 | The two refusal classes are distinguished in both directions | `tests/handlers/test_spam_handlers.py::TestTargetAdminErrorClass` | fold the marker into `_PERMISSION_MESSAGE_MARKERS` | RED (3) |
| 20 | A refused ban leaves the message in place | `tests/handlers/test_spam_handlers.py::TestBanBeforeDelete::test_a_refused_ban_leaves_the_message_in_place` | let the delete run on `target_admin` | RED |
| 21 | The ban runs before the delete | `…::test_the_ban_runs_before_the_delete` | restore `asyncio.gather` | RED |
| 22 | `ban_user_for_spam` reports its outcome | `tests/handlers/test_spam_handlers.py::TestBanOutcomeVocabulary` | make every path report `banned` | RED (6) |
| 23 | The credit is refunded when the target turns out to be an admin | `tests/handlers/message/test_pipeline.py::TestSpamDeleteChargeGuards::test_refunds_when_target_is_an_admin` | refund only on `spam_delete_failed` | RED |
| 24 | A migration adds the three columns and backfills `activated_at` idempotently | `tests/database/test_schema_migrations.py` | skip the backfill / skip an ALTER | RED |

## Rows that share one revert, deliberately

Rows 2 and 3 are the two halves of one partition — a roster-only admin is
skipped, a demoted payer is not — so one revert (`validation` reading
`admin_ids` again) turns both RED. That is the property being tested: the split
is only correct if both directions hold.

Rows 7 and 8 likewise share the `activated_at` mutex: keying on row existence
instead breaks the second `/start` *and* denies the welcome to a prefilled
roster row. Rows 13 and 14 share the demotion path (flag, never insert).

## The row that was green for the wrong reason

Row 9 originally asserted the CLI **help text** (`--apply … default is a
read-only dry run`), which is a string, not a behaviour: flipping the function's
own default to `apply=True` left all 14 tests in the file green. The default
argument is exactly what a caller who forgets the flag gets, so it is now pinned
by calling `sync_group_administrators(group_id)` with no `apply` at all and
counting the rows on disk. Under the flipped default it fails; restored, it
passes.

This is why the matrix is checked by reverting rather than by reading the test
names: a green test under a broken step is read as coverage, and it is worse
than no test at all.

## Command

```bash
python3 -m pytest \
  tests/database/test_group_operations.py \
  tests/database/test_admin_operations.py \
  tests/database/test_schema_migrations.py \
  tests/handlers/message/test_validation_pre_existing.py \
  tests/handlers/test_chat_member_joins.py \
  tests/handlers/test_status_handlers.py \
  tests/handlers/test_spam_handlers.py \
  tests/handlers/message/test_pipeline.py \
  tests/test_sync_group_admins.py \
  tests/background_jobs/test_scheduled_jobs.py -q
```

Full suite at the tip: **1221 passed, 4 skipped, 4 deselected**.
`python3 tools/audit.py`: **HEALTHY (GREEN), 54 gates**.
`ruff check src`: clean.
