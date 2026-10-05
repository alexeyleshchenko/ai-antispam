# ai-antispam — Pacemaker cron procedures

**Owns:** the procedure behind every `ai-antispam-*` pacemaker cron. The crons are THIN WAKES (P7 — cron is a pacemaker, not the worker): each one wakes its owning lane and stops. Everything substantive lives HERE, never in a cron prompt — a cron prompt cannot be updated by a skill change, so work left in one goes stale silently.

Landed 2026-10-04 (board #76, meta-factory #118 shape (b)): 13 rows that carried their work order in the prompt (or posted it into a channel) now wake the owning lane instead. The cron prompt names the section below; the woken lane runs it and reports into its own topic.

Lane session ids — Outreach `acc3fa9b-cefa-4e35-bf87-422696e558f0` · Bot `6d921dca-fb0a-455b-bceb-dfb78dcf1f07` · HQ `cb06a94a-be02-4e8c-b6c6-c8c9f09922f4` · Triage `6ca0d547-4a72-4c29-ac10-967daa98af0a`.

**Board migration 2026-10-04:** the service board moved from `alexeyleshchenko/ai-antispam` to `leshchenko1979/ai-antispam` (owner order; Open Questions q16 = option B, "renumber everything"). All 62 items were recreated on the canonical repo and RENUMBERED — the old→new map is `docs/board-migration/number-map.json`. Where a gate below names its OLD board number in prose (`#43`, `#52`, kept as the gate's stable identity), the live issue number is the one given beside it. The third close-gate, `ai-antispam-62-close-gate` (cron `f7d756b6-cead-412f-9291-3585ec218d13`), has no section here: it was RETIRED 2026-10-02 (ruling q13, ledger n=152 — its subject closed on mechanism evidence as canonical `#95`, board #62 pre-migration) and its cron was DELETED 2026-10-04, because its `trigger_cmd` still named the retired `alexeyleshchenko` account and a disabled cron carrying a dead-account literal is exactly the silently-stale class this file exists to prevent.

---

## Outreach lane

**wave0-reply-sweep** (daily 12:00 MSK) — run the wave-0 reply sweep. The procedure is the `outreach-reply-sweep` skill; read it first and follow every HARD RULE and step exactly. Absolutes: never send to admins or targets (admin replies are DRAFTS quoted in the report, marked «NOT SENT — awaiting Alexey»); OWNER_HANDLED — re-read each full thread before any closure/courtesy draft, any reply from @leshchenko1979 (id 133526395) after our pitch means owner-handled, no draft, no closure status change; donor bearer is runtime-only from `apps:/data/projects/forwarder/.env` (`BEARER_TOKEN`), never printed or persisted, `/tmp` copies wiped; any `FLOOD_WAIT` stops the run immediately with the remaining seconds reported, no retries; stars spent must be 0. Work in `/root/ai-antispam-outreach` (`git pull --ff-only` first), append findings to `plans/marketing-plan-monoforum-outreach.md` as the next Appendix F.N, commit, and report the commit hash.

**wave0-unactivated-reprobe** (Tue 12:00 MSK) — `python3 /root/ai-antispam-outreach/outreach/scripts/reprobe_weekly.py`. It stages the donor bearer at runtime, paces at 5.5 s, stops on any FLOOD_WAIT, scans `chats[]` for a monoforum-flagged supergroup, reads the donor wallet, and journals to `logs/reprobe_YYYYMMDD.log`. Targets: grebenukm, ranarod, ruslan_rentaved, kutergin_on_fire, necopia — one journal row per target per day, and finish an interrupted run in a second pass. RED ALERT if @grebenukm's inbox appears (monoforum supergroup, id == int("107" + channel_id), title ending "Messages") — put it at the very top of the report, DO NOT SEND. Update `recon/recon-wave0-queue.md` rows (inbox status + date) and commit that one file. Wallet balance is INFO ONLY — never emit a refill warning and never ask for a top-up (owner directive 2026-09-06). Never hand-invoke MTProto methods from a prompt (campaign #13).

**outreach-db-sync** (daily 09:15 MSK) — `cd /root/ai-antispam-outreach && python3 outreach/scripts/export_db.py`. The Postgres `outreach` schema is the SINGLE OPERATIONAL WRITER for campaign state; this job only mirrors it into git (`export/*.jsonl`, one commit if changed). Do NOT run `etl_sends.py`. Report one line: commit sha + table counts.

**outreach-mining-tranche** (Mon/Wed/Fri 06:15 UTC) — zero-stars law: only 0★-inbox candidates are usable, paid-wall finds are skipped-only, never gate on wallet, never ask for Stars.
1. `cd /root/ai-antispam-outreach && git pull --ff-only` (report failure, continue).
2. Seed by weekday: Mon = grebenukm, Wed/Fri = lazyproducer. Pool args are BARE FILENAMES (`--pool greb_pool_deep.json` or `mine_lazy_pool.json`); if the pool file is missing, re-run with `--pool none` EXPLICITLY and say so — never report a silent fresh run.
3. Launch DETACHED: `nohup python3 outreach/scripts/mine_greb_deep.py --seed <seed> --data-tag mine_<tag> --pool <poolfile> > outreach/data/<tag>.log 2>&1 &` then end the turn with "tranche launched".
4. OLDER-POSTS RULE (owner 2026-09-07): if the run walks the same post range as the last tranche, immediately re-launch with `--from-id` below the last walked id.
5. The NEXT same-seed run commits the previous tranche's artifacts to main and PUSHES (`git -C /root/ai-antispam-outreach push origin main`; owner 2026-09-24 retired the do-not-push injunction).
Report: posts walked (id range), senders total, NEW, FullUser resolved, qualified ≥500 (paid>0 marked SKIPPED), pool size, commit sha, pushed (yes/no).

**outreach-auto-kick** (daily 07:00 UTC) — owner order 2026-09-07 ("auto-kick in future"); zero-stars law as above.
1. `cd /root/ai-antispam-outreach && git pull --ff-only`.
2. `ps aux | grep mine_greb_deep | grep -v grep` — if a miner is running, report "tranche already in flight" and END.
3. Count free (paid=0) qualified candidates not yet staged; if ≥10 are ready, report feedstock status and END.
4. Otherwise launch DETACHED: `nohup python3 outreach/scripts/mine_greb_deep.py --seed lazyproducer --data-tag mine_kick_$(date +%m%d) --pool mine_lazy_pool.json > outreach/data/mine_kick_$(date +%m%d).log 2>&1 &`, then end with "auto-kick tranche launched".
5. OLDER-POSTS RULE as above; do NOT wait for completion.
Report one line: trigger reason (feedstock count), seed, pool size, launch confirmation.

**outreach-watch-poll** (07:00 every 6 h) — `python3 /root/ai-antispam-outreach/outreach/scripts/watch_poll.py` (LONG, ~5-10 min: 15 channels × MTProto history + LLM gate; it runs detached — wait for the background result, do NOT re-run). The trigger is unconditional because the cron trigger budget is a hard 30 s and the poll needs minutes (2026-09-18). Emit the table with columns Channel, Observed, Spam, Deleted, Alive, Deletion Speed (avg, min-max), Last Post (UTC), plus stream-daemon status and recent deletions. Zero sends, zero stars, observe-only.

**outreach-stream-joins** (daily 00:00 UTC) — run exactly `bash /root/ai-antispam-outreach/outreach/stream/join_with_service.sh`, once. The wrapper stops `outreach-watch-stream.service`, runs `join_watch.py --live`, and ALWAYS restarts the daemon (EXIT trap) — never call `join_watch.py` directly and never add your own stop/start. It joins AT MOST ONE group and re-mutes the cohort; the 5 joins/day spread is enforced across runs by `/root/ai-antispam-stream/join_state.json` — do NOT loop, retry, or pass `--budget`. Report the `-- totals --` block verbatim plus the `joined` / `failed(...)` lines; quote any `PAUSED:` reason. Never send to a target; never retry a FLOOD_WAIT; on non-zero exit report the last ~15 stderr lines.

**stream-liveness-check** (every 6 h) — run the row-age probe (this was the cron trigger; it is now the lane's own measurement):
`timeout 25 python3 -u /root/ai-antispam-outreach/outreach/scripts/stream_liveness_check.py; rc=$?; echo "check_exit=$rc"`
Exit 0 means the capture is live — say so in one line and stop. Non-zero means report the probe output verbatim and state ONE reading: `STREAM CAPTURE STALE` (the capture session stopped receiving; a fresh heartbeat beside it means the SESSION is blind, not the daemon — campaign #14) · `STREAM LIVENESS UNMEASURABLE` (the probe could not read the store) · `check_exit=124` (the 25 s cap killed the check — not measured, treated as blind). Next action line: check `outreach-watch-stream.service` and `/root/ai-antispam-stream/l1979_ru.session`; a fresh login needs the owner (phone + 2FA) — campaign #14. NEVER infer liveness from `heartbeat.json` or `systemctl is-active` (that signal read healthy through the 16.4 h outage).

---

## Bot lane

**bot-service-health** (daily 09:15 MSK) — `cd /root/ai-antispam && python3 scripts/bot_health_probe.py`. Its stdout IS the report — reproduce it verbatim, do not summarise, reformat or re-word it. A non-zero exit (1 = WARN, 2 = ALERT) means prefix one line `⚠️ verdict=<verdict> — <reasons>`. If the probe itself fails to run (exit ≥ 3, traceback, ssh failure), say so in one plain line and stop — do NOT hand-run its checks to fill the gap. Full procedure, thresholds, the 2026-09-12 baseline and per-signal triage: `skills/ai-antispam/health-check.md`. Never print the bot token.

**timeout-monitor** (daily 12:00 UTC) — run the widened effectiveness receipt (this was the cron trigger; it is now the lane's own measurement, preserved here verbatim):

```bash
CT=$(ssh apps "docker ps --filter name=ai-antispam --format '{{.Names}}' | head -1") && START=$(ssh apps "docker inspect -f '{{.State.StartedAt}}' $CT") && ssh apps "docker logs --since $START $CT 2>&1 | awk '/app.agents: Gateway spam classification failed/{n++} END{print n+0}'" > /tmp/oc_tm_fb && ssh apps 'docker exec postgres psql -U postgres -d ai_spam_bot -At -c "SELECT '"'"'all='"'"'||count(*)||'"'"' decided='"'"'||count(*) FILTER (WHERE status='"'"'decided'"'"')||'"'"' failed='"'"'||count(*) FILTER (WHERE status='"'"'failed'"'"')||'"'"' | pre-pool n='"'"'||count(*) FILTER (WHERE created_at < TIMESTAMPTZ '"'"'2026-09-25T16:17:46Z'"'"')||'"'"' failed='"'"'||count(*) FILTER (WHERE created_at < TIMESTAMPTZ '"'"'2026-09-25T16:17:46Z'"'"' AND status='"'"'failed'"'"')||'"'"' | post-pool n='"'"'||count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '"'"'2026-09-25T16:17:46Z'"'"')||'"'"' failed='"'"'||count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '"'"'2026-09-25T16:17:46Z'"'"' AND status='"'"'failed'"'"')||'"'"' | span '"'"'||to_char(min(created_at),'"'"'MM-DD HH24:MI'"'"')||'"'"' .. '"'"'||to_char(max(created_at),'"'"'MM-DD HH24:MI'"'"') FROM classification_verdicts;"' > /tmp/oc_tm_v && printf 'container=%s start=%s | gateway_fallbacks=%s (since container start) | %s\n' "$CT" "$START" "$(cat /tmp/oc_tm_fb)" "$(cat /tmp/oc_tm_v)" && rm -f /tmp/oc_tm_fb /tmp/oc_tm_v
```

Report it verbatim. Field guide: `all= / decided / failed` are durable (every `classification_verdicts` row ever written); `pre-pool n= / failed=` is the baseline the current pool must beat; `post-pool n= / failed=` is the candidate window; `span` is the window's first/last timestamps; `container=` is resolved DYNAMICALLY by name filter (a deploy can leave it hash-prefixed, e.g. 0cf2fa311a3c_ai-antispam); `gateway_fallbacks` is since CONTAINER START ONLY — the container log dies on every recreate, so it is never a durable count. Pool-swap boundary 2026-09-25T16:17:46Z; the pool is dots-studio/dots-3-note-preview:free (accuracy lead) + liquid/lfm-2.5-2.6b:free (availability net). Never read a post-pool zero as proof until n ≥ 29 (the 95% upper bound 3/n first falls below a 10.39% baseline at n=29). Aggregates cannot prove a specific rescue — that needs the per-message log join. Reason language is a live acceptance axis: the fallback model must write `reason` in Russian; a CJK `reason` is the defect the owner banned ling for — report it.

**43-close-gate** (daily 12:05 UTC) — the cron `trigger_cmd` is kept as a FIRE GATE only (a date guard AND the item — board #43 pre-migration, now `leshchenko1979/ai-antispam#76` — still open). The close window anchors at the **live deploy 2026-10-03T00:44:25Z** (image built 2026-10-03T00:43:44Z; container `ai-antispam-ai-antispam-1`), NOT the fix deploy 2026-09-24T11:33:19Z — HQ ruling on #43, `7a7ab39` rev.2. The ≥72 h window cannot complete before **~2026-10-06T00:44:25Z**, so the gate can fire while the window is still open: when it does, state the window's age and do NOT close. When woken, run all three instruments and reproduce their output verbatim:

1. The fire gate — the cron `trigger_cmd` above, kept only to decide whether to wake. It is NOT the durable instrument:

```bash
[ "$(date -u +%Y%m%d)" -ge 20260927 ] && /usr/local/bin/gh issue view 76 --repo leshchenko1979/ai-antispam --json state 2>/dev/null | grep -q OPEN && echo GATE-DUE
```

2. The durable instrument — `classification_verdicts` survives container recreates; the container log does NOT. Read the store on the apps host:

```bash
ssh apps "docker exec postgres psql -U postgres -d ai_spam_bot -At -c \"SELECT 'all='||count(*)||' decided='||count(*) FILTER (WHERE status='decided')||' failed='||count(*) FILTER (WHERE status='failed')||' | since-live-deploy n='||count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '2026-10-03T00:44:25Z')||' failed='||count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '2026-10-03T00:44:25Z' AND status='failed')||' | span '||to_char(min(created_at),'MM-DD HH24:MI')||' .. '||to_char(max(created_at),'MM-DD HH24:MI') FROM classification_verdicts;\""
```

Read as of 2026-10-05T12:18:08Z: `all=1699 decided=1608 failed=91 | since-live-deploy n=453 failed=0 | span 09-28 08:36 .. 10-05 11:14`.

3. The log instrument, coverage stated — resolve the container DYNAMICALLY (a deploy can leave it hash-prefixed, and a hardcoded name then fails with "No such container"), read `StartedAt`, and count `Webhook processing timed out after` (the OLD path) and `Classification pending after` (the NEW path).

Interpretation: the #43 fix has two halves — `handle_timeout` (webhook cancelled the work) is the OLD path, `Classification pending after` (the classification runs detached and the redelivery serves the stored verdict) is the NEW one; a healthy post-fix system shows the new path firing and the old one not. A `failed` store row means a message went unclassified and therefore unmoderated. A zero is not proof unless the window is long enough — state n beside any zero (95% upper bound 3/n vs a 10.39% baseline ⇒ n ≥ 29). If the log window is shorter than 72 h, SAY SO plainly — the close must rest on the durable store, not a log a deploy truncated. Do NOT close the issue, edit any file, or investigate further — the Bot lane owns the close and acts on the line.

**52-close-gate** (daily 11:30 UTC) — the cron `trigger_cmd` is kept as a FIRE GATE only (deploy 2026-09-27T11:20:14Z; window ends 2026-09-28T11:20:14Z; the item — board #52 pre-migration, now `leshchenko1979/ai-antispam#85` — still open). When woken:

1. Measure the window (read rc first-hand): `ssh apps 'docker logs --since 2026-09-27T11:20:14 ai-antispam-ai-antispam-1 2>&1 | grep -c "Unhandled exception in dispatcher"'`, the same for `callback_handlers`, and `wc -l`. Counting trap: each record double-prints (structured logger + Python logging handler) — state which count you are quoting. A container recreate TRUNCATES the log; if `StartedAt` is later than the window start, say so and re-base rather than presenting a short window as the full one.

2. If the dispatcher count is 0 and the log covers the window: confirm the artifact (`ssh apps 'docker exec ai-antispam-ai-antispam-1 md5sum /app/app/handlers/callback_handlers.py'` must equal the repo md5), CLOSE THE BOARD FIRST, then write the close row carrying `board=closed`. Do NOT put a close/fix keyword immediately before #N in a commit subject (#56). Ledger: intake n=65 is HQ's, claim n=51 exists; the closer writes the close row.

3. If the count is non-zero: do NOT close — report the lines verbatim, name the handler, and state whether the cause is the #52 class or something new.

---

## HQ lane

**self-audit-daily** (daily 08:50 MSK) — Process 3 (Internal Self-Audit). Run, in order:

1. `git -C /root/ai-antispam pull --ff-only`
2. `cd /root/ai-antispam && python3 tools/audit.py --report`
3. Commit ONLY the scorecard that run produced (`evidence/scores/<date>-self-audit.md`), message `evidence: daily self-audit <date>`, then `git push alexey main` and `git push origin main`. Stage `evidence/ledger.jsonl` ONLY if it actually changed — another lane may have committed rows since your pull.
4. Report the verdict line (HEALTHY / DEGRADED), the artifact path, and — only if DEGRADED — the failing leg verbatim.

WHY NO `--stamp` (do not put it back — board #65, ledger n=86): `tools/ledger.py` derives the writing identity from `OPENCRABS_SESSION_ID` and refuses any session it cannot place in a fragment's lanes; a cron session is never a lane, so `--stamp` is refused by construction. Do NOT retry it, do NOT pass a different `--actor`, and do NOT redirect `OC_LEDGER_PATH`. The `run` row this job used to stamp is OWED BY THE HQ LANE on receipt — end the report with the line `stamp owed: run row for <date>` and the HQ lane writes it.

VERDICT PREDICATE (`tools/audit.py:339`): the verdict is `gates AND cadence` only. First-pass yield and rework rate are displayed but NEVER enter the verdict — never name them as a failing leg.
