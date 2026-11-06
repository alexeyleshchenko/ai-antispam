# #130 — backfill dry run, measured against the live fleet

Receipt for the roster backfill. The script is `scripts/sync_group_admins.py`; the read
is `getChatAdministrators` per active group, and the write pass (when it runs) is
`sync_group_administrators(chat_id, apply=True)` — the same function the daily sweep
calls, so the preview and the write agree by construction.

## How it was run

The live database is `PG_HOST=postgres` inside the compose network, so the dry run ran in
a throwaway container on the app's own image, on the `traefik-public` network, with the
committed tree mounted at `/app`:

```
ssh apps 'docker run --rm --network traefik-public \
  --env-file /data/projects/ai-antispam/.env \
  -v /tmp/ai-antispam-130:/app -e PYTHONPATH=/app \
  ghcr.io/leshchenko1979/ai-antispam:main python3 scripts/sync_group_admins.py --json'
```

Read-only: `--apply` was not passed, so nothing was written to `group_administrators`.

## First attempt, before the migration

Every group answered `UndefinedColumnError: column "is_admin_now" does not exist` — 53
groups, 53 errors, `seats=0`. That is the honest signature of running the new script
against a database the deploy has not migrated yet. The migration was then applied through
the SHIPPED path (`app.database.trust_operations.ensure_trust_tables`, the function the app
calls at startup — not hand-written SQL):

```
group_administrators: ['is_admin_now', 'is_registered']
administrators: ['activated_at']
administrators activated_at set: 805/805
```

`activated_at` backfilled for all 805 rows from `created_at`, so the prefill cannot
double-grant the 100 initial credits.

## The dry run, 2026-11-06

53 active groups, 43 answered, **10 errors**, 94 admin seats over 37 already-known rows.

| | |
|---|---|
| Groups synced | 43 of 53 |
| Telegram admin seats | **94** |
| Already known | 37 |
| **New seats to write** | **57** |
| Demotions (row present, no longer admin) | 5 |

The ten failures are fleet facts, not script defects: 8 `chat not found` (the bot is gone
from a group still marked active), 1 `member list is inaccessible`, 1 `bot was kicked`, and
1 `TelegramMigrateToChat` (`-5111243184` → `-1003721264646`).

### The groups that carry the new seats

| chat_id | title | telegram | known | new | demoted |
|---|---|---|---|---|---|
| -1003133142605 | Тишина — сообщество инвесторов в недвижимость | 18 | 1 | **17** | 0 |
| -1003110588626 | Чат 𝕹𝕰𝖃𝖀𝕾 𝕾𝕿𝖀𝕯𝕴𝕺𝕾 | 7 | 1 | 6 | 0 |
| -1002101315110 | Место Силы. CHAT Бизнес Клуб Сочи | 7 | 1 | 6 | 0 |
| -1004428873948 | 🦀 Open Crabs Official 🦀 | 4 | 1 | 3 | 0 |
| -1003983522811 | ЧАТ Капитал в Недвиге | 4 | 1 | 3 | 0 |
| -1001867292885 | Чат Прибыльная недвижимость | 4 | 1 | 3 | 0 |
| -1001846673305 | Земля со Светланой Федоровой Chat | 4 | 1 | 3 | 0 |
| -1001827737099 | Чат Кремнев Антон | 4 | 1 | 3 | 0 |

«Тишина» reads **18 admins, 1 known, 17 new** — the group whose admin was classified at
0.90 and had his message deleted.

## Why `--apply` has not run yet

The write must land in the same window as the deploy, not before it. The roster rows are
written with `is_registered = FALSE`, and it is the NEW `get_group` that filters on that
column — the running (old) code reads every `group_administrators` row into `admin_ids`
with no filter at all. Applying the backfill against the old code would therefore put 57
strangers into the list that `no_rights.py` and the billing readers consume, which is the
one hazard the design names as its most important invariant. The backfill runs immediately
after the deploy, and the daily sweep (`sweep_group_administrators`) would fill the roster
within a day even if it did not.
