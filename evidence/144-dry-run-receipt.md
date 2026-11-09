# #144 — no-rights leave-leg split: live dry-run receipt

Measured 2026-11-09, from INSIDE the deployed container
(`ai-antispam-ai-antispam-1`, image `ghcr.io/leshchenko1979/ai-antispam:main`,
recreated 2026-11-09T19:17Z from commit `0d9add1`).

Why the container and not a laptop: the class this issue splits on is Telegram's
own answer for the bot's membership, and the DB stores no rights columns. The
deployed container is the only place holding both the credentials and the code
being verified.

## 1. The population the grace predicate selects

`get_groups_with_no_rights_past_grace(grace_days)`:

```
grace_days=7 -> []            (nothing is past grace yet)
grace_days=0 -> 5 groups      (the whole flagged set)
```

Postgres, `status='active' AND no_rights_detected_at IS NOT NULL`:

| group_id | title | mod | flagged_at | approved | admin_rows |
|---|---|---|---|---|---|
| `-1002107631154` | SV CLUB | t | 2026-11-09 11:12:45.356649Z | 0 | 1 |
| `-1003751652625` | Сервер Shadowraze | f | 2026-11-09 18:57:07.748713Z | 1 | 1 |
| `-1003078404548` | Квантовые технологии | t | 2026-11-09 18:57:07.977847Z | 0 | 2 |
| `-1002496127723` | блатные чупани😎🐾 | t | 2026-11-09 18:57:08.273039Z | 2 | 1 |
| `-1002509060912` | ش كش محادثة | t | 2026-11-09 18:57:08.292564Z | 0 | 0 |

## 2. The deployed decision, per group

Run with the grace job's OWN branch condition (`isinstance(member,
ChatMemberAdministrator)` plus both rights), not a re-implementation:

```
-1002107631154  LEAVE       (unreadable: TelegramBadRequest)
-1003751652625  HOLD+STAY   (administrator missing: del=True  res=False)
-1003078404548  LEAVE       (status=member)
-1002496127723  HOLD+STAY   (administrator missing: del=False res=False)
-1002509060912  LEAVE       (status=member)
```

Split: **2 held, 3 leave.** The two held are exactly the degraded-administrator
class HQ's ruling names, and `-1002496127723` is the paying one (96 credits,
2 approved members) whose data `perform_complete_group_cleanup` would have
deleted on the 16th.

## 3. One-shot, observed live

The 19:17Z boot sweep logged:

```
rights_flagged: 0, rights_cleared: 0, rights_unverified: 0, rights_degraded=0
```

Zero, because all five clocks were already set: every group took the `held`
branch, and no new detection means no payload and no DM. That is the one-shot
property working on real data, not only in the unit test.

## 4. Consequence worth stating plainly

The detection DM is FORWARD-ONLY. These five were flagged before the DM existed,
so none of their admins will be told about the CURRENT degradation — including
`-1002496127723`, a paying group. HQ's constraint ("do not re-notify a group
already flagged") makes that the correct behaviour, and it is recorded here so
the silence is a decision rather than a surprise. A backfill would be a
deliberate exception, and it is HQ's to grant, not this unit's to assume.
