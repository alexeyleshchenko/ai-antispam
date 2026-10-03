# #43 close-gate reconciliation — the classifier-budget defect

**Reconciled:** 2026-10-03T12:17:28Z · **Session:** `6d921dca-fb0a-455b-bceb-dfb78dcf1f07` (Bot lane)
**Issue:** `alexeyleshchenko/ai-antispam#43` — "[defect] classifier budget cannot fit the webhook guard — the fallback can never finish, and the deadline cancels the verdict"
**Fix deploy:** 2026-09-24T11:33:19Z · **Fix:** `a66c425` (detach the update feed; shield the work; store the verdict)
**Live deploy (the close window anchors here):** image built 2026-10-03T00:43:44Z · container started **2026-10-03T00:44:25Z** (`ai-antispam-ai-antispam-1`, `docker inspect`).
**Rev.2:** 2026-10-03T12:26:44Z — anchor corrected to the live deploy, and the moderation-leg figure replaced by a measurement (HQ ruling on #43). Rev.1's `Deploy: 2026-09-24T11:33:19Z` named the *fix* deploy, not the window anchor.
**Gate:** `ai-antispam-43-close-gate` (cron `093a0432-9d4d-41f1-95cf-ea05187408ae`, 12:05 UTC daily)

## Verdict

**#43 STAYS OPEN.** Close condition 1 is **not discharged**: the ≥72 h window anchored at the live deploy (2026-10-03T00:44:25Z) is only ~12 h old, and the pre-anchor record already shows the guard tripping (10 old-path events between the fix deploy and 09-30).

The issue's own terms: *"All three, or the item stays open."*

| # | Condition | Required | Measured | Result |
|---|---|---|---|---|
| 1 | post-deploy `Webhook processing timed out after` count over a window ≥72 h starting at the deploy | 0 | **10** in the fix-deploy window (Logfire, 09-24..09-30); live-anchored window only ~12 h old, 0 so far | **NOT DISCHARGED** |
| 2 | replays served — no second classification per `(chat_id, message_id)` | yes | redeliveries serve the stored verdict in 0.02–0.08 s | PASS |
| 3 | no regression — full suite green + container running the new image | yes | CI green; image `ghcr.io/leshchenko1979/ai-antispam:main` | PASS |

## Condition 1 — the 10 events (Logfire, exact timestamps)

The container log reads 0 because a recreate at 2026-10-03T00:44:25Z truncated it to 11 h 20 m. Logfire is the untruncated source. These 10 events sit **inside the fix-deploy window** (after 2026-09-24T11:33:19Z, before the 10-03 live deploy) — the guard tripped on the detached work's tail even with the fix in place. The live-anchored window that can discharge condition 1 begins 2026-10-03T00:44:25Z and cannot complete before ~2026-10-06T00:44:25Z.

| start (UTC) | elapsed |
|---|---|
| 2026-09-24 17:41:09 | 65.49 s |
| 2026-09-27 12:01:52 | 55.01 s |
| 2026-09-28 04:45:39 | 61.74 s |
| 2026-09-28 12:31:32 | 55.00 s |
| 2026-09-28 18:23:35 | 55.89 s |
| 2026-09-29 08:05:00 | 106.88 s |
| 2026-09-29 08:05:00 | 88.77 s |
| 2026-09-29 11:25:09 | 63.42 s |
| 2026-09-30 07:29:13 | 59.89 s |
| 2026-09-30 16:29:13 | 55.00 s |

Post-fix the line means *the whole detached handler exceeded the 55 s guard*. The shielded work still completes and the message is not lost, so the line is no longer a message-loss signal — it is a wall clock on the detached work.

**Measured (Logfire spans, `limit=2000`), not assumed:**
- moderation leg (`verdict._moderate_and_record`): n=231 since the live deploy — **p50 0.28 s · p95 0.62 s · p99 0.98 s · max 14.35 s** (3 >10 s, 0 >15 s); since 10-01 the max is 24.65 s.
- whole detached work (`verdict._finish`): n=231 since the live deploy — **p50 7.49 s · p95 24.52 s · p99 42.01 s · max 49.82 s**; 28 of 231 exceeded 15 s.

The moderation leg is **not** a persistent ≈19.8 s — rev.1 carried that as an unbacked worst case and HQ flagged it. The design's own reserve is **10 s** (`llm_budget.py`: `WEBHOOK_RESERVE_SECONDS = 10.0`; config comment `45 + 10 <= 55`), and the measured moderation leg sits inside it at p95. The guard is tripped by the **tail of the whole detached work**, not by the budget arithmetic per se: `_finish` p99 42.01 s / max 49.82 s against a 55 s wall is a thin margin, and the pre-10-01 outer-guard events (elapsed 55–107 s) are that tail crossing it. The ticket's first half — *the budget cannot fit the guard* — is therefore **not** closed; the correct framing is that the guard is a wall on the detached work's tail, not on the LLM budget.

## Condition 1 — the trend

Zero old-path events after 09-30. 478 NEW-path `Classification pending` records over the same span (09-24..10-03): the guard returns PENDING and redelivery serves the verdict. **Since the live deploy** (2026-10-03T00:44:25Z → 12:26:44Z): OLD **0**, NEW 6 — the only post-anchor window that exists so far, far short of the ≥72 h the condition demands.

## Reconciliation of the 95 store failures

`classification_verdicts` as of 2026-10-03T12:26:44Z: **1647 total · 1552 decided · 95 failed · 1 live · 0 pending** (rev.1 read 1639/1544 at 12:17:28Z — `decided` grew, `failed` did not).

| day (`created_at`) | failed |
|---|---|
| 09-26 | 4 |
| 09-28 | 12 |
| 09-29 | 44 |
| 09-30 | 34 |
| 10-02 | 1 |

- `failed` = **every LLM leg was exhausted; no verdict** — retryable, not terminal, bounded by `max_attempts`. It is *not* the guard-cancellation signature (which leaves `pending`, or produces the old-path line).
- `reason` is NULL on all 95 (not recorded).
- Closure: **78 `terminal_gone` + 16 `terminal_skipped`** (the `91376e4` close-row fix) + **1 live** (`-1003133142605`/3061, 09-30 15:41 — `terminal_unresolvable`).
- The rows cluster in the ПВЗ chats (`-1001822193445`, `-1002195337276`) the q15 re-enable targeted, plus `-1001846673305`, `-1001867292885`, `-1003983522811`, `-1001470245049`, `-1001503592176`, `-1002101315110`.
- Since 10-01: **293 decided, 1 failed** (created 10-02, closed `terminal_gone`).

## Reading

Both residual signals — the old-path line and the store's `failed` count — go quiet from 10-01. The 95 are attributable to the pre-10-01 LLM/gateway degradation, not to a persisting guard-cancellation defect: the cancellation path is fixed (nothing is lost), while the *budget-fits-the-guard* half of the ticket remains measurable.

## Path to close

A clean ≥72 h window with **0** old-path events, anchored at the **live deploy 2026-10-03T00:44:25Z** (the 10-03 recreate — not 10-01). A ≥72 h window therefore cannot complete before **~2026-10-06T00:44:25Z**. Re-run the gate then: Logfire, `message LIKE '%Webhook processing timed out%'`, window `2026-10-03T00:44:25Z` → +72 h. Since the live deploy (as of 2026-10-03T12:26:44Z): OLD **0**, NEW 6.

Separately worth a decision (the ticket's own first half): the guard is a 55 s wall on the detached work's tail (`_finish` p99 42 s, max 50 s) while the LLM budget plus its 10 s reserve fit comfortably. Widening `system.webhook_timeout` or trimming the tail would stop the guard tripping on slow-but-successful handlers; as written, a trip costs a redelivery, not a message.
