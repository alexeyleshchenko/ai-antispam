# Design: Chat-topic signal for the spam classifier

**Status:** Draft for review (not implemented) — **Phase 1: manual-only**
**Repo:** `/root/ai-antispam` @ `main` (`0cb5571`)
**Author:** R2-D2 (grill-for-unknowns pass + code re-verification)
**Date:** 2026-08-15

---

## 1. Context

### Problem
The classifier judges a message on user profile, reply context, linked channel, and
account signals. It does **not** know what a monitored chat is *about*. Off-topic spam
in a focused chat (e.g. a crypto offer in a PHP job group) is detectable in principle,
but today the model has no baseline of "what topics are normal here", so relevance-based
signals (reply-relevance, knowledge-sharing bait, "unrelated to the discussion") fire
weakly or not at all.

### Target state (Phase 1)
Every admin can give a monitored chat a **topic profile** via a **manual `/scan`**
command (private-DM, interactive pick from the admin's groups, callback-driven — the
same pattern as `lang_set:`/`buy_stars:`). The scan reads recent messages, derives what
the chat is about, stores it, and the description is fed into the classifier as an
additional context signal; a short version shows per-group in `/stats`.

**Explicitly postponed to Phase 2 (out of Phase 1):** automatic scan on chat
activation, periodic refresh (6-month), NULL-backfill sweep of pre-existing chats.
Phase 1 is *all manual* — `/scan` is the only trigger.

### Intent
- Better spam/legit separation via on-topic relevance as a signal, not a hard rule.
- Zero behavior change for chats where the signal is absent (never scanned → classify
  without it, same as today).
- Cost is human-paced: every LLM derivation is triggered by an explicit admin action.
  No background job, no surprise billing.

---

## 2. Verified territory (anchors, checked against `main` @ `0cb5571`)

| Concern | Where it lives | Notes |
|---|---|---|
| Classification entry | `src/app/spam/spam_classifier.py` → `async def is_spam(comment, admin_ids, context)` | Builds system prompt + JSON request, runs gateway agent w/ OpenRouter fallback |
| Context dataclass | `src/app/types.py:249` `SpamClassificationContext` | Fields: name, bio, linked_channel, stories, reply, profile_photo_age, is_premium, is_channel_sender, account_signals_snapshot; `include_*_guidance` properties gate prompt sections |
| Prompt builder | `src/app/spam/prompt_builder.py` `SpamPromptBuilder` | Fluent `add_*_guidance()` sections; `build_system_prompt()` (line 306) chains conditionally; `format_spam_request()` (line 375) emits JSON keys: message, user_name, user_bio, linked_channel, stories, reply_context, account_signals |
| Guidance precedent | `prompt_builder.py:185` `add_reply_context_guidance()` | Multi-paragraph section; `format_spam_request` separate. Same pattern for chat topics |
| Message pipeline | `src/app/handlers/message/pipeline.py:133` `handle_moderated_message()` | `chat_id = message.chat.id` (145), `group` via `validate_group_and_check_early_exits` (148), `classify_spam(...)` at 173 — group object available to inject topic |
| MTProto history fetch | `src/app/spam/user_profile.py` `collect_channel_summary_by_id()` (317) → `_fetch_recent_posts_content()` (456) → `client.call("messages.getHistory", params=_build_history_params(...))` → `_extract_message_text()` (497) | Proven path for channel posts. Reuse for topic scan |
| Group history fallback | `src/app/spam/user_context_utils.py:649` | Discussion-group `messages.getHistory` fallback exists (used for peer-resolution context) — plain-group fetch is not unproven, just used differently |
| Peer resolution | `bot_api_chat_id_to_mtproto` (mtproto_utils) | Channels addressed by username or MTProto chat id — private channels work via negative-id conversion |
| Group row | `src/app/database/group_operations.py`, `src/app/database/models.py:44` `Group` | `groups` table has `linked_channel_id` (ALTER TABLE precedent at `database_schema.py:195`) |
| **Admin→groups join** | `group_operations.get_admin_groups(admin_id)` (251): `SELECT g.group_id, g.title, g.moderation_enabled FROM groups g JOIN group_administrators ga ON g.group_id = ga.group_id WHERE ga.admin_id = $1` | **This is the /scan picker's data source** — per-admin group list already exists |
| Stats | `src/app/handlers/command_handlers.py:305` `handle_stats_command` (private DM only) → `admin_operations.get_admin_stats` (515) → `get_admin_groups` → per-group line at `command_handlers.py:355-361` | Short topic + age go on the per-group title line |
| **Command convention** | ALL admin commands are **private-DM only**: `Command("stats")` (305), `Command("mode")` (383), `Command("ref")` (428), `Command("lang")` (444) — each `F.chat.type == "private"` | **No in-group command precedent.** `/scan` must be private-DM too |
| Callback pattern | `src/app/handlers/callback_handlers.py:47` `@dp.callback_query(F.data.startswith("lang_set:"))` + `_answer_safe()` (31); `buy_stars:` (payment_handlers.py:66); `spam_example:` (private_handlers.py:445) | `/scan` picker reuses this exact shape: `scan_chat:<group_id>` |
| Command menu | `src/app/bot_commands.py` `_COMMAND_IDS` + `setup_bot_commands()` | `/scan` must be added to the list + localized in `locales/{en,ru}.yaml` |
| Scheduled loop | `src/app/background_jobs/scheduled_tasks.py:64` `scheduled_jobs_loop()` → `run_scheduled_jobs()` daily | **Untouched in Phase 1** — refresh sweep deferred |
| Config | `config.yaml` `spam:` section, `recent_posts_limit: 5` precedent | New keys follow this pattern |
| Migrations | `src/migrations/migrate.py`, `database_schema.py` `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` precedent | Idempotent column adds |

---

## 3. Design (Phase 1 — manual-only)

### 3.1 Schema (PostgreSQL, `groups` table)

```sql
ALTER TABLE groups ADD COLUMN IF NOT EXISTS topic_description   TEXT;
ALTER TABLE groups ADD COLUMN IF NOT EXISTS topic_description_short VARCHAR(255);
ALTER TABLE groups ADD COLUMN IF NOT EXISTS topic_updated_at    TIMESTAMPTZ;
```

- `topic_description` — full derived description (what the chat is about, who posts,
  tone, typical topics).
- `topic_description_short` — 1-line version for `/stats`.
- `topic_updated_at` — last successful scan; `NULL` = never scanned.
- Follows the existing `username` / `linked_channel_id` idempotent-ALTER precedent.
- `Group` model (`models.py`) gains the three fields; `get_group()` SELECT updated.

### 3.2 Topic scan service — new module `src/app/spam/chat_topics.py`

```
async def scan_chat_topics(group_id: int) -> ChatTopicScanResult
```

1. **Resolve peer** — reuse `get_mtproto_client()` + `bot_api_chat_id_to_mtproto`
   (same as `collect_channel_summary_by_id`).
2. **Fetch recent messages** — `messages.getHistory` with
   `_build_history_params(peer, limit=topic_scan_limit)` (reuse from `user_profile.py`;
   refactor the shared helpers so both callers use them). Fetch cap **100** (config
   `topic_scan_limit`) — this is the *fetch* bound, not the LLM-input bound.

3. **Filter**:
   - **Channel** (has `linked_channel_id` / is protected): fetch the **channel peer
     directly** — channel posts are by definition owner-authored; this is the "filter
     by channel owner" requirement.
   - **Plain group / discussion group**: take all messages; exclude the bot's own
     messages and obvious admin commands (`/`-prefixed). Owner-filter does not apply
     to groups — all members' messages are the discussion.
   - **Private channel where the peer is unreadable via the bot session**: fall back
     to the linked discussion group's messages (already a NEGATIVE-result pattern in
     `collect_channel_summary_by_id` — mirror its error handling).

4. **Trim sample to a hard context budget** (worst case 100 × 4096 chars ≈ 100K+
   tokens — see the cap fix below; `topic_scan_limit` now 30, so worst case is
   30 × 4096 chars, further bounded by `topic_max_total_chars`).
   tokens would blow every rotation model — cap is mandatory). Two layers, newest-first:
   - **Per-message:** truncate each sampled message text to `topic_max_message_chars`
     (default 500 chars, head kept — topic signal lives at the start).
   - **Total corpus budget:** keep adding messages newest→oldest only until the summed
     text exceeds `topic_max_total_chars` (default 16,000 chars ≈ 4K tokens — fits any
     gateway/OpenRouter model in the classifier rotation; config knob, same `spam:`
     section). Messages past the budget are dropped, not error.
   - Result: bounded input, deterministic, no tokenizer dependency (char math).
   - **Empty sample**: if filtering+trimming leaves no text (common for media-only
     chats — `_extract_message_text` skips caption-less media), skip the LLM call
     entirely: keep the existing description, or use the chat title when no scan
     exists yet. Never spend a derivation call on an empty corpus.

5. **Derive** a topic profile from the sampled text via the LLM (see 3.3).
6. **Store** — `UPDATE groups SET topic_description, topic_description_short,
   topic_updated_at = NOW() WHERE group_id = $1`.
7. **Failure** — any exception/LLM-failure path: log with chat context, leave the
   row untouched, return FAILED. Classification behavior is unchanged (no topic
   signal = current behavior). **Retry = the admin re-runs `/scan`.**

### 3.3 Derivation agent — `src/app/agents.py`

New pydantic-ai agent alongside `get_gateway_spam_agent()`:

```
get_topic_summary_agent()  # gateway model + OpenRouter fallback, same routing as classifier
TopicSummary(BaseModel):
    description: str          # 2-4 sentence profile
    short_description: str    # ≤ 120 chars, stats-safe
```

Input: sampled message texts (truncated, deduplicated, newest-first). Prompt:
"Summarize what this chat is normally about…". Structured output validated by
pydantic. **Fallback semantics (resolved):** a **first** scan that fails at
derivation → chat title as both description and short (state is never empty). A
**re-scan** that fails → keep the existing (usually richer) description untouched —
never clobber a good description with a bare title. The row is only written on
success or first-scan title fallback.

### 3.4 Classifier integration

1. `SpamClassificationContext.chat_topics: str | None = None` + property
   `include_chat_topics_guidance` (`self.chat_topics is not None`).
2. `prompt_builder.add_chat_topics_guidance()` — new section:

   > ## CHAT TOPIC CONTEXT
   > The "chat_topic" section describes what this chat is normally about. Messages
   > that are off-topic for this chat (promos, scams, irrelevant offers) are elevated
   > spam signals. On-topic messages are NOT evidence of legitimacy on their own —
   > a relevant comment from a spam-looking profile remains high-risk (Trojan Horse).
   > Never classify a message as spam *solely* because it is off-topic; use it to
   > weight existing signals. Topic descriptions can be stale (manual refresh) —
   > treat them as a heuristic, not ground truth.

3. `build_system_prompt()` — append the section when `include_chat_topics_guidance`.
4. `format_spam_request()` — new JSON key `"chat_topic"` (null when absent).
5. `pipeline.handle_moderated_message()` — `group` is already loaded; set
   `ctx.chat_topics = group.topic_description` on the context built for `classify_spam`.

### 3.5 `/scan` command + picker — `command_handlers.py` + `callback_handlers.py`

**Private DM only** (`F.chat.type == "private"`), matching every existing admin
command. **No argument form**: interactive picker.

1. `@dp.message(Command("scan"), F.chat.type == "private")` → `handle_scan_command`:
   - Load `get_admin_groups(admin_id)` (same source `/stats` uses).
   - If empty → `stats.no_groups` message. If one group → scan it directly.
   - Else → inline keyboard, one `InlineKeyboardButton` per group, callback
     `scan_chat:<group_id>`, title-truncated to button limits. (Pagination if the
     admin has many groups — post-MVP.)
2. `@dp.callback_query(F.data.startswith("scan_chat:"))` → re-validate the caller is
   an admin of that group (`get_admin_groups`), then:
   - Ack immediately (`_answer_safe`).
   - Reply "scanning…" (async feedback, since MTProto fetch + LLM takes seconds).
   - Run `scan_chat_topics(group_id)`.
   - Success → confirm with the new short description + age.
   - FAILED → explain failure (access rights / flood / LLM), row untouched, suggest
     re-running `/scan`.
3. Register `scan` in `bot_commands.py` `_COMMAND_IDS` + `locales/{en,ru}.yaml`
   (`bot_commands.scan` + scan result/error strings).

**Why DM+callback, not in-group:** (a) zero in-group command precedent — every admin
command is private-DM; (b) an in-group `/scan` would land in the moderation pipeline
subscriber too, and it's unverified whether the command handler preempts it — DM
avoids that collision entirely; (c) the admin can scan a chat without joining it.

### 3.6 `/stats` display

- `get_admin_groups()` SELECT gains `topic_description_short, topic_updated_at`.
- Per-group line (`command_handlers.py:355-361`): append
  `│ <short description>` to the title line when present; omit when `NULL` (no layout
  change for chats without a topic). When present, show **age** from `topic_updated_at`
  (e.g. `│ php jobs · 3d old`) so staleness is visible — manual refresh means a topic
  stays until the admin re-runs `/scan`.

### 3.7 Config (`config.yaml`, `spam:` section)

```yaml
topic_scan_limit: 30         # recent messages fetched per topic scan (FETCH bound)
topic_max_message_chars: 500 # per-message truncation head cap (context bound layer 1)
topic_max_total_chars: 16000 # total corpus chars budget → LLM input ≈4K tokens (context bound layer 2)
```

- `topic_refresh_days` / `topic_scan_enabled` / activation-launch: **NOT in Phase 1**
  (deferred with the scheduled job). Keep the schema columns live; the refresh query
  code simply does not exist yet.
- Context bounds are **char-based**, not token-based: deterministic, no tokenizer
  dependency. 16,000 chars ≈ 4K tokens is comfortable for every model in the
  classifier rotation (both the gateway agent and the OpenRouter fallback).

---

## 4. Failure modes & mitigations

| Failure | Effect | Mitigation |
|---|---|---|
| MTProto fetch fails (no rights, private channel, flood) | No topic for that chat | Log + FAILED result; row untouched; error shown to the admin in DM; re-run `/scan`. Mirror `collect_channel_summary_by_id` error handling |
| Derivation LLM fails / gateway down | No topic update | First scan → title fallback (state never empty); **re-scan → keep existing description, never clobber with title**; empty sample → skip LLM entirely |
| Wrong/stale topic (chat pivoted) | Off-topic heuristics misweighted | Guidance text says "heuristic, not ground truth, never sole reason"; **age shown on /stats** so staleness is visible; `/scan` re-runs anytime |
| Admin scans a chat they don't moderate | — | Re-validate via `get_admin_groups` in the callback handler before scanning |
| Cost | Human-paced by design | Every derivation is admin-triggered; sample capped at 100 msgs; no background job in Phase 1 |
| **LLM input exceeds context window** (long messages) | Truncated/token-truncated topic description | **Hard caps: per-message 500 chars + total corpus 16,000 chars (≈4K tokens)** — bounded input, guaranteed to fit any rotation model; past-budget messages dropped, not error |
| Forum supergroups (real topics) | Per-chat description is coarse | Out of scope today (classifier doesn't specialize topics); flagged as future work in doc |
| In-group `/scan` collision with moderation pipeline | — | Avoided by design: DM-only command |

---

## 5. Testing

- **Unit** (mirror existing `src/tests/` layout):
  - Prompt builder: section appended iff `include_chat_topics_guidance`.
  - `format_spam_request`: `chat_topic` key present/null correctly.
  - Context property gating (None → no guidance).
  - DB: column add idempotency (run ALTER twice), get_group returns new fields,
    store/clear topic.
  - Scan filtering: channel → channel-peer only; group → excludes bot's own +
    `/`-commands; private-channel fallback to discussion.
  - **Spam self-exclusion (accepted 2026-08-17): deleted spam is gone from
    Telegram, so getHistory never returns it (handle_spam → bot.delete_message);
    residual = kept low-confidence spam only. No message-ID→outcome index
    (schema cost > benefit).**
  - **Context trim: per-message truncation to 500 chars (head kept); total corpus
    stops at 16,000 chars (newest-first); 100 long 4096-char messages → bounded
    ≤16K chars input, and the derivation call provably fits the window**
    (property-style test with worst-case synthetic samples).
  - **Empty sample handling**: media-only chats (0 text after filtering) → no LLM
    call, title fallback; existing description kept on re-scan of empty corpus
  - `get_admin_groups` returns the new fields; picker build: 0 / 1 / many groups.
- **Integration**: `/scan` from DM → keyboard → callback → scan → stored → `/stats`
  shows short topic + age; `/scan` on a group the caller doesn't moderate → refused.
- **Classification regression**: same message classified with vs without a topic
  signal — verify off-topic spam gets *elevated* weight but a clean on-topic message
  with a spammy profile is still caught (Trojan Horse preservation).

---

## 6. Implementation steps (Phase 1, ordered)

1. **Schema** — `database_schema.py` idempotent ALTERs + `models.py` Group fields +
   `get_group()` SELECT + `get_admin_groups()` SELECT (new fields).
2. **Refactor shared MTProto helpers** out of `user_profile.py`
   (`_build_history_params`, `_extract_message_text`, `_fetch_recent_posts_content`-like)
   for reuse by `chat_topics.py`.
3. **`src/app/spam/chat_topics.py`** — scan/filter/derive/store + FAILED handling.
4. **`agents.py`** — `get_topic_summary_agent()` + `TopicSummary` schema + fallback.
5. **Prompt/context** — `types.py` field + property; `prompt_builder.add_chat_topics_guidance`;
   `format_spam_request` key; `pipeline.py` injection.
6. **Commands/UI** — `handle_scan_command` + `scan_chat:` callback; `bot_commands.py`
   `_COMMAND_IDS` + locales; `/stats` short-topic + age line.
7. **Config** — `topic_scan_limit` key + example comment.
8. **Tests** — per §5.
9. **Docs** — update repo docs (design memo) + deploy via the normal apps path.

Phase 2 (postponed): activation launch, daily NULL-backfill sweep, 6-month refresh,
`topic_refresh_days` config key.

---

## 7. Open items / decisions locked

- ✅ **Phase 1 = manual-only; `/scan` is the sole trigger** (user, 2026-08-15).
- ✅ `/scan` **private-DM interactive picker** of the admin's groups, callback-driven
  (user, 2026-08-15 — chose "DM without arg — interactive list of my groups").
- ✅ Auto backfill + periodic refill **postponed to Phase 2** (user, 2026-08-15).
- ✅ Channel = scan channel peer (owner posts by construction); group = all messages
  minus bot's own/system.
- ✅ Topic **age shown on /stats** so manual-refresh staleness is visible.
- ⏳ Forum-topic specialization: future work, out of scope.
- ⏳ Picker pagination for admins with many groups: post-MVP.
- ⏳ Guardrail: if mid-build the MTProto group-fetch probe fails on a real box
  (blindspot #1 from the first grill), the scan falls back to the bot-API
  `getChatHistory`-equivalent path if available, else the feature ships
  channel-only initially with groups following once probe passes.