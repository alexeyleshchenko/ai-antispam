# #43 close-gate reconciliation — the classifier-budget defect

**Reconciled:** 2026-10-03T12:17:28Z · **Session:** `6d921dca-fb0a-455b-bceb-dfb78dcf1f07` (Bot lane)
**Issue:** `alexeyleshchenko/ai-antispam#43` — "[defect] classifier budget cannot fit the webhook guard — the fallback can never finish, and the deadline cancels the verdict"
**Deploy:** 2026-09-24T11:33:19Z · **Fix:** `a66c425` (detach the update feed; shield the work; store the verdict)
**Gate:** `ai-antispam-43-close-gate` (cron `093a0432-9d4d-41f1-95cf-ea05187408ae`, 12:05 UTC daily)

## Verdict

**#43 STAYS OPEN.** Close condition 1 is falsified by the durable record.

The issue's own terms: *"All three, or the item stays open."*

| # | Condition | Required | Measured | Result |
|---|---|---|---|---|
| 1 | post-deploy `Webhook processing timed out after` count over a window ≥72 h starting at the deploy | 0 | **10** (Logfire, 09-24..09-30) | **FAIL** |
| 2 | replays served — no second classification per `(chat_id, message_id)` | yes | redeliveries serve the stored verdict in 0.02–0.08 s | PASS |
| 3 | no regression — full suite green + container running the new image | yes | CI green; image `ghcr.io/leshchenko1979/ai-antispam:main` | PASS |

## Condition 1 — the 10 events (Logfire, exact timestamps)

The container log reads 0 because a recreate at 2026-10-03T00:44:25Z truncated it to 11 h 20 m. Logfire is the untruncated source.

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

Post-fix the line means *the handler took longer than the 55 s guard*. The shielded work still completes, so no message is lost — but the guard still trips, i.e. the budget arithmetic (`llm.budget_seconds` 45 s + the moderation leg ≈ 19.8 s) still does not fit inside `system.webhook_timeout` 55 s. The ticket's first half ("the budget cannot fit the guard") is **not** closed.

## Condition 1 — the trend

Zero old-path events after 09-30. 478 NEW-path `Classification pending` records over the same span (09-24..10-03): the guard returns PENDING and redelivery serves the verdict.

## Reconciliation of the 95 store failures

`classification_verdicts` as of 2026-10-03T12:17:28Z: **1639 total · 1544 decided · 95 failed · 1 live · 0 pending**.

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

A clean ≥72 h window with 0 old-path events and 0 new `failed` rows. The earliest fully-clean window begins 10-01, so it is satisfiable from ~10-04. Re-check at the gate then. Also worth a decision: widen `system.webhook_timeout` (or shrink the budget) so the guard stops tripping on slow-but-successful handlers — the ticket's own first half.
