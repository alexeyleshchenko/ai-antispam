# ai-antispam — Pacemaker cron procedures

**Owns:** the procedure behind every `ai-antispam-*` pacemaker cron. The crons are THIN WAKES (P7 — cron is a pacemaker, not the worker): each one wakes its owning lane and stops. Everything substantive lives HERE, never in a cron prompt — a cron prompt cannot be updated by a skill change, so work left in one goes stale silently.

Landed 2026-10-04 (board #76, meta-factory #118 shape (b)): 13 rows that carried their work order in the prompt (or posted it into a channel) now wake the owning lane instead. The cron prompt names the section below; the woken lane runs it and reports into its own topic.

Lane session ids — Outreach `acc3fa9b-cefa-4e35-bf87-422696e558f0` · Bot `6d921dca-fb0a-455b-bceb-dfb78dcf1f07` · HQ `cb06a94a-be02-4e8c-b6c6-c8c9f09922f4` · Triage `6ca0d547-4a72-4c29-ac10-967daa98af0a`.

**Board migration 2026-10-04:** the service board moved from `alexeyleshchenko/ai-antispam` to `leshchenko1979/ai-antispam` (owner order; Open Questions q16 = option B, "renumber everything"). All 62 items were recreated on the canonical repo and RENUMBERED — the old→new map is `docs/board-migration/number-map.json`. Where a gate below names its OLD board number in prose (`#43`, `#52`, kept as the gate's stable identity), the live issue number is the one given beside it. The third close-gate, `ai-antispam-62-close-gate` (cron `f7d756b6-cead-412f-9291-3585ec218d13`), has no section here: it was RETIRED 2026-10-02 (ruling q13, ledger n=152 — its subject closed on mechanism evidence as canonical `#95`, board #62 pre-migration) and its cron was DELETED 2026-10-04, because its `trigger_cmd` still named the retired `alexeyleshchenko` account and a disabled cron carrying a dead-account literal is exactly the silently-stale class this file exists to prevent. The two remaining close-gates were RETIRED 2026-10-09 (board #115): `ai-antispam-43-close-gate` (subject canonical `#76`, closed 2026-10-09T12:08:58Z) and `ai-antispam-52-close-gate` (subject canonical `#85`, closed 2026-10-04T20:50:19Z) — each `trigger_cmd` fired only while its subject issue was `OPEN`, so both went permanently silent while every run still read `status=success`; both crons were DELETED and their sections removed.

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
CT=$(ssh apps "docker ps --filter name=ai-antispam --format '{{.Names}}' | head -1") && START=$(ssh apps "docker inspect -f '{{.State.StartedAt}}' $CT") && ssh apps "docker logs --since $START $CT 2>&1 | awk '/app.agents: Gateway spam classification failed/{n++} END{print n+0}'" > /tmp/oc_tm_fb && ssh apps 'docker exec postgres psql -U postgres -d ai_spam_bot -At -c "SELECT '"'"'all='"'"'||count(*)||'"'"' decided='"'"'||count(*) FILTER (WHERE status='"'"'decided'"'"')||'"'"' failed='"'"'||count(*) FILTER (WHERE status='"'"'failed'"'"')||'"'"' | post-pool n='"'"'||count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '"'"'2026-09-25T16:17:46Z'"'"')||'"'"' failed='"'"'||count(*) FILTER (WHERE created_at >= TIMESTAMPTZ '"'"'2026-09-25T16:17:46Z'"'"' AND status='"'"'failed'"'"')||'"'"' | span '"'"'||to_char(min(created_at),'"'"'MM-DD HH24:MI'"'"')||'"'"' .. '"'"'||to_char(max(created_at),'"'"'MM-DD HH24:MI'"'"') FROM classification_verdicts;"' > /tmp/oc_tm_v && printf 'container=%s start=%s | gateway_fallbacks=%s (since container start) | %s\n' "$CT" "$START" "$(cat /tmp/oc_tm_fb)" "$(cat /tmp/oc_tm_v)" && rm -f /tmp/oc_tm_fb /tmp/oc_tm_v
```

Report it verbatim. Field guide: `all= / decided / failed` is the RETAINED window — the `classification_verdicts` rows still inside `verdict_ttl_days` (`config.yaml:20`; pruned by `cleanup_old_verdicts`, `src/app/background_jobs/scheduled_tasks.py:81`). It is a ROLLING window, NOT every row ever written: the table is TTL-pruned, so `all=` shrinks as rows age out — `max(id)` is the ever-written count, `all=` the retained subset. `post-pool n= / failed=` is the candidate window; the pool-swap boundary (2026-09-25T16:17:46Z) is now older than the retention, so it equals `all=`. `span` is the window's first/last timestamps; `container=` is resolved DYNAMICALLY by name filter (a deploy can leave it hash-prefixed, e.g. 0cf2fa311a3c_ai-antispam); `gateway_fallbacks` is since CONTAINER START ONLY — the container log dies on every recreate, so it is never a durable count. The pool is ag/gemini-3.7-flash-high (accuracy lead) + cb/deepseek-v4.1-flash (availability net), repointed off OpenRouter at 736ad0f. Never read a zero as proof until n ≥ 29 (the 95% upper bound 3/n first falls below a 10.39% baseline at n=29). Aggregates cannot prove a specific rescue — that needs the per-message log join. Reason language is a live acceptance axis: the fallback model must write `reason` in Russian; a CJK `reason` is the defect the owner banned ling for — report it.

---

## HQ lane

**self-audit-daily** (daily 08:50 MSK) — Process 3 (Internal Self-Audit). Run, in order:

1. `git -C /root/ai-antispam pull --ff-only`
2. `cd /root/ai-antispam && python3 tools/audit.py --report`
3. Commit ONLY the scorecard that run produced (`evidence/scores/<date>-self-audit.md`), message `evidence: daily self-audit <date>`, then `git push alexey main` and `git push origin main`. Stage `evidence/ledger.jsonl` ONLY if it actually changed — another lane may have committed rows since your pull.
4. Report the verdict line (HEALTHY / DEGRADED), the artifact path, and — only if DEGRADED — the failing leg verbatim.

WHY NO `--stamp` (do not put it back — board #65, ledger n=86): `tools/ledger.py` derives the writing identity from `OPENCRABS_SESSION_ID` and refuses any session it cannot place in a fragment's lanes; a cron session is never a lane, so `--stamp` is refused by construction. Do NOT retry it, do NOT pass a different `--actor`, and do NOT redirect `OC_LEDGER_PATH`. The `run` row this job used to stamp is OWED BY THE HQ LANE on receipt — end the report with the line `stamp owed: run row for <date>` and the HQ lane writes it.

VERDICT PREDICATE (`tools/audit.py:339`): the verdict is `gates AND cadence` only. First-pass yield and rework rate are displayed but NEVER enter the verdict — never name them as a failing leg.
