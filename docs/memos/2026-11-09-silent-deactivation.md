# Silent deactivation — which groups die without anyone being told

**Date:** 2026-11-09
**Status:** descriptive — measurement only, no code changed
**Trigger:** issue #141, dispatched to the Bot lane by Triage after #140 shipped

## The question

`moderation_monitor` guards one direction: a group with a **paying** admin whose
moderation is **off** (the #41 class). #141 asks whether the inverse is guarded —
a group with moderation **on** and **nobody able to pay**, which deactivates on
its first billable spam message.

## The chain, read line by line

| Step | Where | What it does |
|---|---|---|
| 1 | `try_deduct_credits.py:53` | `deduct_credits_from_admins(chat_id, amount)` |
| 2 | `group_operations.py:165` | picks the highest-balance `BILLABLE_SEAT`; returns `0` when none covers `amount` |
| 3 | `try_deduct_credits.py:63-65` | logs **WARNING** `No paying admins in chat … for <reason>` |
| 4 | `try_deduct_credits.py:66` | calls `handle_deactivation(chat_id)` |
| 5 | `try_deduct_credits.py:95` | `set_group_moderation(chat_id, False, …)` — **unconditional**, before it knows whether anyone can be told |
| 6 | `try_deduct_credits.py:107-115` | `get_chat_administrators` failure → logs, returns (moderation already off) |
| 7 | `try_deduct_credits.py:183` `find_min_credits_admin` | skips bots and non-admin members; keeps an admin **only if `get_admin(...)` returns a row** (`:205`) |
| 8 | `try_deduct_credits.py:119` `if min_credits_admin:` | notify path |
| 9 | `try_deduct_credits.py:178-180` `else` | logs **INFO** `No min-credits admin found … — nothing to notify` |

The predicates, from `src/app/database/roster_predicates.py`:

- `REGISTERED_SEAT` (`:56`) = `ga.is_registered = TRUE`
- `BILLABLE_SEAT` (`:59`) = `ga.is_registered AND ga.is_admin_now`
- `PAYING_SEAT` (`:64`) = `BILLABLE_SEAT AND a.credits > 0`

`deduct_credits_from_admins` deliberately uses `BILLABLE_SEAT` plus a balance
comparison rather than `PAYING_SEAT`, because a `credits > 0` filter there would
hide the insufficient-balance path that deactivates a group (`roster_predicates.py:60-63`).

## Where the silence actually comes from

Step 9 is the hole, and it has a precise shape: **`find_min_credits_admin` returns
`None` when no live administrator has an `administrators` row at all.** That is the
never-opened-the-bot case. In that state:

- the group is deactivated (step 5 already happened),
- no DM is attempted (there is no `admin_id` to address),
- the group fallback message is not posted either — it lives inside the same
  `if min_credits_admin:` block,
- and the only trace is an **INFO** line.

`TelegramLogHandler` is constructed with `super().__init__(level=logging.WARNING)`
(`src/app/common/telegram_logging_handler.py:40`), so INFO never reaches the
operator chat. The `WARNING` at step 3 does, which is why the *event* is not
invisible to the operator — the **group's admins** are the ones who learn nothing.

So #141's phrasing needs one correction: a zero-payer group does not always die
silently. It dies silently only when **no live admin has an `administrators` row**.

## Measured 2026-11-09, live `ai_spam_bot`, after #140

```
status   moderation_enabled  no_payer  groups
active   f                  t         2
active   t                  f         41
active   t                  t         2
left     f                  t         4
left     t                  f         6
left     t                  t         17
paused   f                  t         4
paused   t                  t         2
```

Active groups = **45** (all moderation states). Of those, **43** carry moderation
on, and of those **2** have `paying = 0`. The two split by whether anyone is
notifiable:

| Group | id | roster | live admin with `administrators` row | deactivation outcome |
|---|---|---|---|---|
| Квантовые технологии | `-1003078404548` | 2 | **yes** (`1164885034`, credits 0) | **loud** — min-credits admin found, DM + group message attempted |
| ⁨ش كش⁩ محادثة | `-1002509060912` | 0 | no | **silent** — INFO only |

Квантовые технологии rows: `1164885034` `is_registered=f` `is_admin_now=t`
credits `0`; `7279043661` `is_registered=t` `is_admin_now=f` credits `66` (the
departed admin #140 correctly stopped billing). Its `paying = 0` and
`live_with_row = 1`, so it takes the notify path.

## Neither group has ever processed a message

| Group | `classification_verdicts` | `message_lookup_cache` | `last_active` |
|---|---|---|---|
| `-1003078404548` | 0 | 0 | `2026-02-12 15:46:26.187429+00` |
| `-1002509060912` | 0 | 0 | `2025-04-08 23:10:14.09405+00` |

`last_active` equals `created_at` to the microsecond on both, and both tables that
record a processed message are empty. So the class is **armed but has never
fired**: no message has reached `try_deduct_credits` for either group. The defect
is a live code path, not a live incident.

## The monitor's blind spot

`get_paid_groups_with_moderation_off` (`group_operations.py:1703-1737`) is
`status = ACTIVE AND moderation_enabled = false AND {PAYING_SEAT}` — the inverse
of the state above. `check_unmoderated_paid_groups`
(`background_jobs/moderation_monitor.py:20`) reports it with `logger.error`, which
does reach the operator through the Telegram log handler. There is no leg for
`moderation_enabled = true AND zero PAYING_SEAT`.

The monitor's own docstring (`moderation_monitor.py:30-34`) explains why it does
not repair: the same flag is false by design while a group awaits admin rights, so
a blind repair would re-create the defect in a second writer. That reasoning
applies unchanged to the inverse leg — it must **report**, not repair.

## What is NOT decided here

The disposition — warn the live admins, auto-pause, or deactivate loudly — is a
product decision. It is issue #141's third close condition and it is not the Bot
lane's to make.
