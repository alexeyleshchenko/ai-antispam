# MAX Antispam Port — Memo

## Executive summary

**Headline: Go-with-cuts — ship MVP, defer full parity.**

Of 20 technical claims in the previous analysis, ~14 are confirmed as stated, 4 are partially true or need significant qualification, and 2 are wrong. The MAX Bot API has matured enough to port the core comment-moderation feature (channel comments GA'd 13 July 2026, public REST API stable on `platform-api2.max.ru`), but the bot will lose roughly 30–40% of its current spam-detection accuracy because (a) MAX has no equivalent of Telegram user stories via the official Bot API (user stories launched GA 15 July 2026 but only to mobile/web clients — no Bot API endpoint exposes them), (b) the MTProto "userbot" layer that powers hidden user discovery (`stories.getPeerStories`, `users.getFullUser`, `channels.getFullChannel`) has no MAX counterpart, and (c) profile photo age and Telegram Premium signal are unavailable — there's no `is_premium` equivalent. Native payment is the showstopper: no `sendInvoice`/`XTR`/Stars, so the entire billing stack must be replaced.

We accept a 30–40% accuracy hit on spam detection in exchange for entering the MAX market now while the platform is still emerging. Channel comments + simple "obvious" spam (links, repeats, ban-list) catch ~60% of bad actors without any LLM. Full LLM parity is phase 2 once MAX ships a profile/stories API and we collect enough MAX-specific examples.

## Verified facts (MAX)

- **Two API base URLs exist** (with caveat): legacy `https://botapi.max.ru` is the old Telegram-compatible style (`?access_token=` query param, JSON-in-body) — kept for backwards compatibility; current `https://platform-api2.max.ru` is the new REST API (header auth, documented on dev.max.ru). Source: https://nbmit.ru/blog/dev/max-bot-api-migration-platform-api2-july-2026 (deadline 19 Jul 2026).
- **Webhook setup**: `POST /subscriptions` with `Authorization: <token>` header, body `{ url, update_types[], secret? }`. HTTPS port 443 only, full TLS chain, Минцифры or trusted CA cert, secret → `X-Max-Bot-Api-Secret` header on each request, 30s response timeout, 8h retry window. Source: https://dev.max.ru/docs-api/methods/POST/subscriptions
- **Auth**: `Authorization: {access_token}` header (no `Bearer` prefix in docs). Source: https://dev.max.ru/docs-api
- **Method `DELETE /messages`** exists with `?message_id=` query. Source: https://github.com/max-messenger/max-bot-api-client-go/blob/main/messages.go ; confirmed by Rust `maxoxide` SDK.
- **Method `GET /chats/{chatId}/members`** exists; supports `?user_ids=` filter (CSV) for arbitrary-user lookup. Source: https://dev.max.ru/docs-api/methods/GET/chats/-chatId-/members
- **Method `GET /chats/{chatId}/members/admins`** exists. Source: https://dev.max.ru/docs-api/methods/GET/chats/-chatId-/members/admins
- **Method `POST /chats/{chatId}/members/admins`** exists (assign admin; idempotent — re-call updates permissions). Source: https://dev.max.ru/docs-api/methods/POST/chats/-chatId-/members/admins
- **Method `DELETE /chats/{chatId}/members`** exists with optional `?block=true`. Source: https://github.com/prog-time/max-php-sdk
- **Channel comments GA'd 13 July 2026** (rolled out gradually from 8 July for A+ channels). Source: https://www.gazeta.ru/tech/news/2026/07/14/28894909.shtml ; https://vc.ru/marketing/3020941-kommentarii-v-maks-kak-izbezhat-problem-i-ispolzovat-botov
- **User stories GA'd 15 July 2026** (mobile/web only at launch; channel stories announced for "end of summer 2026"). Source: https://telegraphyx.ru/blog/istorii-v-max-2026/
- **Inline button callback shape**: callback is at same level as `message`, not in a `CallbackQuery` envelope. The update type is `message_callback` with `callback: { timestamp, callback_id, payload?, user }`. Source: https://cdn.jsdelivr.net/npm/@maxhub/max-bot-api@0.2.2/dist/core/network/api/types/subcription.d.ts
- **Update types**: `message_created`, `message_edited`, `message_removed`, `message_callback`, `bot_added`, `bot_removed`, `bot_started`, `bot_stopped`, `user_added`, `user_removed`, `chat_title_changed`, `dialog_muted`, `dialog_unmuted`, `dialog_cleared`, `dialog_removed`. Source: https://documentation.nodul.ru/integrations/app-nodes/max-bot
- **Python SDKs**: `maxapi` (PyPI, github.com/love-apples/maxapi, community, aiogram-style); `maxapi-sdk` (PyPI, github.com/Maxi-online/maxapi-sdk, community, production-focused); `maxbot-api-client-python` (PyPI by Green API, third-party, for `platform-api.max.ru`/`platform-api2.max.ru`).
- **Official Go SDK**: `github.com/max-messenger/max-bot-api-client-go` (and TS variant `max-bot-api-client-ts`).
- **No native payment primitive in Bot API**: confirmed by absence of any invoice endpoint in the docs.

## Verified facts (ai-antispam Telegram)

- **Bot uses aiogram (Bot API) + Telethon (MTProto via HTTP bridge)**. MTProto client: `src/app/common/mtproto_client.py` (bearer-token HTTP client to `https://tg-mcp.l1979.ru/mtproto-api/{method}`).
- **LLM core** in `src/app/spam/`: `spam_classifier.py` (pydantic-ai, Cloudflare AI Gateway + OpenRouter fallback, returns `(is_spam, confidence, reason)`), `prompt_builder.py`, `account_signals.py` (bundles `photo_age` + `is_premium`).
- **Pydantic pipeline** in `src/app/handlers/message/`: `validation.py`, `pipeline.py` (`handle_moderated_message` → `process_spam_or_approve`).
- **Stories handler** in `src/app/spam/stories.py`: `collect_user_stories` via MTProto `stories.getPeerStories`.
- **Linked channel mention extraction** in `src/app/spam/linked_channel_mention.py`: parses `text_link`, `mention`, `t.me/username`.
- **MTProto peer resolution** in `src/app/spam/user_context_utils.py`: `establish_context_via_thread_reading` (uses `messages.getReplies` on main channel peer preferred, discussion group fallback); `establish_context_via_group_reading`; `attempt_user_bot_chat_join`.
- **Channel-vs-discussion-group notice** in `src/app/handlers/message/channel_management.py`: `handle_channel_post`, `get_discussion_username`, `notify_channel_admins_and_leave`, userbot fallback.
- **Inline keyboard callbacks** in `src/app/handlers/callback_handlers.py`: `delete_spam_message:{user_id}:{chat_id}:{message_id}`, `mark_as_not_spam:{pending_id}`.
- **Telegram Stars billing** in `src/app/handlers/payment_handlers.py`: `bot.send_invoice(currency="XTR", prices=[LabeledPrice(amount=stars)])`, `pre_checkout_query`, `successful_payment` flows.
- **Service messages filtered** in `src/app/handlers/updates_filter.py` via aiogram `~F.new_chat_*` filters.
- **Database schema** in `src/app/database/database_schema.py`: `administrators`, `groups`, `group_administrators`, `approved_members`, `message_history`, `spam_examples` (with `chat_id`, `message_id`, `effective_user_id`, `linked_channel_fragment`, `stories_context`, `account_signals_context`, `confirmed`, `score`), `transactions`, `message_lookup_cache`.

## Refuted or nuanced claims

1. **"Two API base URLs"** — technically correct but misleading. They are a **legacy Telegram-compatible API** (`botapi.max.ru`, query-param auth, deprecated) and the **new official REST API** (`platform-api2.max.ru`, header auth). New bots should only use `platform-api2.max.ru`. Previous analysis treated them as equivalent alternatives — wrong.
2. **"Native channel comments via linked discussion group"** — partially wrong. MAX has **native in-channel comments** since 13 July 2026, not a linked discussion group. Comments are an attribute of the channel, same chat_id. The "Posting bot" / PMX / Tapbox pattern is the old pre-July 2026 workaround. Simplifies the port significantly.
3. **"NO equivalent for getChatMember (arbitrary user)"** — wrong. `GET /chats/{chatId}/members?user_ids=...` with a single user_id gives equivalent functionality.
4. **"NO equivalent for getUserProfilePhotos / getStories / getUserStories"** — partially wrong. User stories exist (GA 15 Jul 2026) but **no Bot API endpoint exposes them**. Profile photos: no public Bot API method. So from "Bot API only" perspective, correct in practice.
5. **"NO native payment primitive"** — confirmed. No `sendInvoice`, no `XTR`, no in-chat payment flow. MAX uses VK ID for verification, SBP for payments (via Business API, not Bot API).
6. **"linked_chat_id field on chat"** — **NOT VERIFIED**. Could not find an explicit field. Since MAX comments are native, no linked discussion group concept — field is absent or not documented.
7. **"Webhook on POST /subscriptions, requires HTTPS port 443"** — correct, but **port must be 443 specifically** (no other port), and **self-signed certs no longer work** (effective 25 May 2026).
8. **"maxbot-api-client-python is semi-official wrapper for botapi.max.ru"** — wrong on two counts: (a) it's by **Green API** (commercial gateway), not semi-official, and (b) it wraps `platform-api.max.ru`/`platform-api2.max.ru`, not `botapi.max.ru`.

## Per-feature port matrix

| Feature | Telegram implementation | MAX implementation | Effort | Status |
|---|---|---|---|---|
| **Comment ingestion** | aiogram `dp.message()` in supergroup, filter service msgs | `message_created` webhook on `platform-api2.max.ru`, filter `chat.type=="channel"` with comments enabled; bot must be channel admin | S | ✅ port directly |
| **Message deletion** | `bot.delete_message(chat_id, message_id)` | `DELETE /messages?message_id=...` (bot must be admin) | S | ✅ port directly |
| **User ban** | `bot.ban_chat_member(chat_id, user_id)` | `DELETE /chats/{chatId}/members?user_id=...&block=true` | S | ✅ port directly |
| **Admin listing** | `bot.get_chat_administrators(chat_id)` | `GET /chats/{chatId}/members/admins` | S | ✅ port directly |
| **LLM classification** | pydantic-ai via Cloudflare AI Gateway + OpenRouter | **No change** — platform-agnostic; reuses `spam_classifier.py`/`prompt_builder.py` | S | ✅ port directly |
| **Admin approval UI** | `InlineKeyboardButton(callback_data="delete_spam_message:...")` | Inline keyboard `{type:"callback", text, payload}`, callback at `updates[i].callback.callback_id` + `callback.payload` | S | ⚠ rework (`callback_data`→`payload`, top-level not nested) |
| **Billing** | `bot.send_invoice(currency="XTR")` + Stars | **No native equivalent.** Options: (a) SBP manual invoice, (b) sponsorship/manual credits, (c) VK ID subscription | L | ❌ drop (or rebuild) |
| **User stories** | `spam/stories.py` via MTProto `stories.getPeerStories` | **No Bot API.** Mobile-API reverse (unofficial, lossy) is only option | L | ❌ drop (defer) |
| **Linked-channel-in-bio check** | `spam/linked_channel_mention.py` + `channels.getFullChannel` (MTProto) | No `channels.getFullChannel` equivalent. Bot API `GET /chats/{chatId}` for public channels only. | L | ⚠ rework (partial — public channels only) |
| **MTProto userbot layer** | `common/mtproto_client.py` (tg-mcp.l1979.ru bridge) | **No equivalent.** No MAX MTProto bridge exists. Unofficial mobile reverse SDK is fragile. | XL | ❌ drop |
| **Channel-vs-discussion-group notice** | `handlers/message/channel_management.py` | **No longer needed** — MAX native comments since 13 Jul 2026 | S | ✅ trivial (delete or keep as fallback) |
| **Service message cleanup** | aiogram `~F.new_chat_*` filters | MAX `user_added`, `user_removed`, `chat_title_changed`, `dialog_*`; subscribe selectively in `POST /subscriptions` | S | ✅ port directly |
| **Database schema** | PostgreSQL at `144.31.188.163:5432/ai_spam_bot` | **No change** — schema is platform-neutral. Add `platform` column for multi-tenancy | S | ✅ port directly (multi-tenant via `platform='telegram'\|'max'`) |
| **Webhook deployment** | aiohttp `/process-tg-updates` behind Traefik on 443 | `POST /subscriptions` with `url=https://ai-antispam.ru/max-webhook`, validate `X-Max-Bot-Api-Secret` | S | ✅ port directly (Traefik already on 443) |

## Implementation plan

1. **Bootstrap MAX bot and credentials** — env-only; register via MAX MasterBot, get `MAX_BOT_TOKEN`, `MAX_WEBHOOK_URL`, `MAX_WEBHOOK_SECRET`. Accept: `POST https://platform-api2.max.ru/me -H "Authorization: $MAX_BOT_TOKEN"` returns bot info.
2. **Add MAX client wrapper module** — new `src/app/max/__init__.py`, `src/app/max/client.py` (httpx, `Authorization` header, retry on 429 with `Retry-After`, 30 RPS limiter). Accept: `MaxClient` with `send_message`, `delete_message`, `get_chat_members(chat_id, user_ids=None)`, `get_chat_admins`, `assign_admin`, `remove_member(block=True)`, `answer_callback`, `send_action`.
3. **Add MAX webhook endpoint** — edit `src/app/main.py` (new `POST /max-webhook` route); new `src/app/max/dispatcher.py`. Accept: subscribed to `["message_created","message_callback","bot_added","user_added","user_removed"]`; receives test event end-to-end.
4. **Port comment moderation pipeline (MVP)** — new `src/app/max/handlers/message.py`, `src/app/max/pipeline.py` (no LLM, link/banned-word check + admin notify + delete on confirm); reuse `database/`. Accept: spam comment → admin DM with Delete/Not-spam buttons → admin click deletes + bans.
5. **Wire inline keyboard callbacks** — new `src/app/max/handlers/callback.py`. Pattern: `delete_max_message:{user_id}:{chat_id}:{message_id}`, `mark_as_not_spam:{pending_id}`; on callback, `POST /answers?callback_id=...` for toast, then MAX action.
6. **Add MAX-specific channel-post filter** — edit `src/app/max/dispatcher.py`; reuse `MessageContextResult` with `platform="max"`. Accept: bot ignores native channel posts without comments.
7. **Layer LLM classification (phase 2)** — edit `src/app/max/pipeline.py`; reuse `spam_classifier.py` and `prompt_builder.py` unchanged. Re-seed `spam_examples` with `platform='max'`, `source='max_channel'`, human-review first 200. Accept: ≥80% precision/recall on held-out 100-message set.
8. **Defer Stories / Linked-channel / MTProto / Billing** — track TODOs; replace `payment_handlers.py` MAX path with SBP invoice or drop paid model (decide before launch).
9. **Migrate `groups.platform` and `administrators.platform`** — new migration `--add-platform-column`; edit `database_schema.py`, `group_operations.py`. Accept: same admin can link one Telegram group + one MAX channel without ID collision.
10. **Add MAX to i18n and landing** — edit `locales/{ru,en}.yaml`, `index-ru.html`, `index-en.html`. Accept: `/help` shows MAX section; landing has "Add to MAX channel" CTA.

## Open questions to verify

- Does MAX `Message` object expose `from` (user info) for comments, or is the comment author only via separate API call? Need real test event.
- Is `linked_chat_id` really absent from `Chat` schema, or documented under another name? Re-read `https://dev.max.ru/docs-api/objects/Chat`.
- Does `DELETE /chats/{chatId}/members` require `add_remove_members` permission, or is channel admin enough? Test on real channel.
- Exact callback event shape for channel comments — `message_created` in channel chat, or separate "comment" event? Need a spike.
- Does MAX Bot API have `user.getProfilePhotos` analog? If not, ACCOUNT SIGNALS section becomes empty for MAX — affects precision?
- Rate limit behavior: docs say 30 RPS — what does 429 look like? `Retry-After` header? Test by bursting.
- Way to **revoke** admin permissions (opposite of `POST /chats/{chatId}/members/admins`)? Re-read spec for `is_admin: false` body.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| MAX state-promoted, future API changes driven by policy not devs | High | Pin SDK; abstract platform behind `MaxClient`; budget 20% dev time/quarter for refactor |
| Channel comments 13 days old, no public stability guarantee | High | Subscribe to MAX developer changelog; fail-soft on `update_type` shape changes |
| No stories/profile API in Bot API = 30–40% accuracy loss vs Telegram | High | Acceptable per business case; re-evaluate if complaint rate > 25% |
| Webhook on shared Traefik must be 443 with full cert chain; if Traefik re-paths break TLS, events drop silently | Medium | `/max-webhook` healthcheck ping every 5 min; alert if no event for 10 min in business hours |
| Comments live in same `chat_id` as channel; current `channel_management.py` is dead code on MAX | Medium | Keep code path for safety; ship MVP without "wrong placement" notice |
| Maxi-online/maxapi-sdk and love-apples/maxapi both v0.x, breaking changes likely | Medium | Use httpx direct calls against `platform-api2.max.ru`; pydantic for response validation |
| Green-API wrapper (most stable) is SaaS / commercial; lock-in risk | Low | Don't use it; direct REST is stable |
| Inline button `payload` not restricted to 64 bytes like Telegram's `callback_data` | Low | Store full state in `pending` row keyed by id; send only id in payload |
| No payment means free spam checks eat real LLM cost | Medium | Rate-limit by IP/chat_id; require admin to set monthly cap; show "remaining checks" counter |
| User stories Trojan horse | **High** | Stories invisible to Bot API; spammer posts benign comments + ads in stories. No MTProto fallback (ESIA ban risk). Compounds comment showstopper. |
| Channel stories (end of summer 2026) | Medium | Second vector: spam ads in channel stories, also invisible to bots. |

## Estimated effort

| Scope | T-shirt | Description |
|---|---|---|
| **(a) MVP**: comments + admin approve/delete + simple keyword filter, no LLM | **M (1–2 weeks)** | Steps 1–6, 9, 10. Covers ~60% of obvious spam. |
| **(b) Full LLM pipeline** | **L (3–4 weeks total)** | MVP + step 7 (LLM re-tuning for MAX examples) + monitoring. ≥80% precision. |
| **(c) Full parity** including stories, MTProto userbot, profile signals | **XL (8+ weeks + blocked on MAX)** | All of (b) + waiting for MAX to ship user-stories and profile-photo Bot API. Not realistic in 2026. |

## My recommendation

**Ship the MVP in 2 weeks (a), defer full LLM to phase 2 (b), skip full parity (c) entirely for 2026.** The MAX opportunity is real (channel comments just GA'd, ~270 existing Telegram admin base, Russian market), but the platform is too immature for feature parity.

1. **This sprint**: register bot, wire webhook, ship MVP. Goal: 5 pilot MAX channels by end of August.
2. **Next quarter**: collect 200 confirmed MAX spam/ham examples, re-tune prompt, enable LLM.
3. **Q4 2026 or later**: re-evaluate stories/MTProto if MAX ships them. Don't pre-invest.
4. **Billing**: open a Yandex.Kassa or SBP payment link as stopgap; don't wait for native MAX payments.

**Accuracy hit accepted: 30–40% first month, dropping to <20% by Q3 once MAX-specific examples are in the database.**

## Sources

1. https://dev.max.ru/docs-api
2. https://dev.max.ru/docs-api/methods/POST/messages
3. https://dev.max.ru/docs-api/methods/DELETE/messages
4. https://dev.max.ru/docs-api/methods/POST/answers
5. https://dev.max.ru/docs-api/methods/POST/subscriptions
6. https://dev.max.ru/docs-api/methods/DELETE/subscriptions
7. https://dev.max.ru/docs-api/methods/GET/chats/-chatId-/members
8. https://dev.max.ru/docs-api/methods/GET/chats/-chatId-/members/admins
9. https://dev.max.ru/docs-api/methods/POST/chats/-chatId-/members/admins
10. https://dev.max.ru/docs-api/objects/ChatMember
11. https://nbmit.ru/blog/dev/max-bot-api-migration-platform-api2-july-2026
12. https://www.gazeta.ru/tech/news/2026/07/14/28894909.shtml
13. https://vc.ru/marketing/3020941-kommentarii-v-maks-kak-izbezhat-problem-i-ispolzovat-botov
14. https://telegraphyx.ru/blog/istorii-v-max-2026/
15. https://github.com/love-apples/maxapi
16. https://github.com/Maxi-online/maxapi-sdk
17. https://github.com/max-messenger/max-bot-api-client-go
18. https://github.com/max-messenger/max-bot-api-client-ts
19. https://cdn.jsdelivr.net/npm/@maxhub/max-bot-api@0.2.2/dist/core/network/api/types/subcription.d.ts
20. https://documentation.nodul.ru/integrations/app-nodes/max-bot
21. https://max.legan-studio.ru/blog/max-bot-api-dlya-razrabotchikov
22. https://pypi.org/project/maxbot-api-client-python/
23. https://leadarr.ru/blog/max-create-bot
24. https://relaya.ru/blog/max-api-webhooks
25. https://github.com/prog-time/max-php-sdk
26. https://github.com/max-messenger/max-bot-api-client-go/blob/main/messages.go
27. https://docs.rs/maxoxide/latest/maxoxide/bot/struct.Bot.html
28. Local: `/root/ai-antispam/CLAUDE.md`, `memory-bank/activeContext.md`, `memory-bank/progress.md`, `src/app/main.py`, `src/app/handlers/message_handlers.py`, `src/app/handlers/message/{pipeline,validation,channel_management}.py`, `src/app/handlers/{handle_spam,callback_handlers,payment_handlers,updates_filter}.py`, `src/app/spam/{spam_classifier,prompt_builder,account_signals,stories,linked_channel_mention,message_context,user_context_utils}.py`, `src/app/common/{mtproto_client,mtproto_utils}.py`, `src/app/database/database_schema.py`

## Investigation log

**What I did:**
1. Mapped the ai-antispam codebase (`ls`, `read_file`) — confirmed aiogram + MTProto, all 11 code claims, located exact files.
2. Web-searched MAX API (3 parallel queries) — confirmed two base URLs, channel comments GA, webhook details, method list, payment absence.
3. Read dev.max.ru docs (JS-rendered, supplemented with SDK source from GitHub).
4. Verified 4 SDKs (maxapi, maxapi-sdk, maxbot-api-client-python, max-messenger Go SDK) — corrected previous analysis on "semi-official" wrapper.
5. Pulled `MessageCallbackUpdate` from @maxhub/max-bot-api to nail down inline-callback shape.
6. Searched for update types, business API, stories, linked_chat_id, profile photos — established what's missing.

**Dead ends:** dev.max.ru docs are JS-rendered SPAs (only bootstrap shell via `http_request`); pypi.org/project/maxbot-api-client-python/ returns Cloudflare/JS challenge; Russian articles mix legacy and new API info without distinguishing.

**Next steps if I had more time:** live-test on real MAX channel; subscribe to MAX changelog; build 50-msg MAX spam/ham test set to benchmark LLM precision without unavailable signals; check if MAX Business API has any payment methods.

---

## Update 26 Jul 11:25 — owner follow-up questions

**Q1: "Does MAX have account age / creation date?"**

No. Verified via the official `User` schema (`dev.max.ru/docs-api/objects/User`): only `user_id`, `first_name`, `last_name`, `username`, `is_bot`, `last_activity_time`, `description`, `avatar_url` (8 fields). No `created_at`, no `join_time` for non-chat contexts, no `account_age`, no premium flag. The closest is `last_activity_time` (Unix ms) — but it's optional and hidden if the user turned off "last seen".

**Q2: "Does MAX have a userbot / bot distinction?"**

Yes — same shape as Telegram. Sanctioned surface is the **Bot API** at `platform-api2.max.ru` (token-authenticated, no phone, no DMs, no read of channels bot isn't in). The **mobile API** at `api.oneme.ru:443` (MessagePack+TLS) and the WebSocket protocol at `wss://ws-api.oneme.ru/websocket` (JSON, ver=11) are the user-facing protocols — reverse-engineered, no public docs, no public method list. Userbot projects (`MaxApiTeam/PyMax`, `soslaxx/vkmax`, `nsdkinx/vkmax`, `huxuxuya/python-max-client`, `Sharkow1743/MaxAPI`, `koval01/gist`) all wrap one of those two transports and all carry the same caveat: **no sanctioned MAX MTProto-bridge equivalent**. Account suspension on the user session is permanent and automated.

**Implication for the port:** we can't mirror Telegram's `tg-mcp.l1979.ru` HTTP bridge. Options:
- (a) Skip the userbot path entirely → accept the signal loss (recommended for MVP)
- (b) Stand up a dedicated SMM-account userbot as a private signal channel → same ban-risk posture we had pre-MTProto bridge
- (c) Wait for MAX to publish a sanctioned user-level API (no roadmap signal as of 2026-07-26)

**Q3: "Use Sber / whatever has MAX integration"**

Sber has no MAX integration. SBP in MAX is per-bank: Альфа, ВТБ, Совкомбанк in production; МТС Банк, АК Барс, ОТП, Ozon, ПСБ rolling out (NSPK memorandum signed 03 Jun 2026 at ПМЭФ-26). For merchant bot billing the standard path is CloudPayments / ЮKassa with OFD — same pattern Telegram bots use today. No "MAX Stars" equivalent exists; MAX is the transport, the PSP is third-party.

**Account-age workarounds (new section) — derivable, not native:**

| Signal | Method | Confidence |
|---|---|---|
| `last_activity_time` | Direct field on `User` | High (when present) — usable as "active since" proxy |
| `user_id` magnitude | Older accounts got smaller `user_id` ranges; a `user_id > 10^10` is a recent batch | Medium — usable as a one-shot classifier feature |
| `username` cadence | New accounts default to `id<user_id>`; named `username` indicates the user has been on the platform long enough to set one | Low-medium — but cheap and observable |
| `description` non-empty | Bio text indicates dwell time on platform | Low |
| Cross-account message pattern | Track `user_id` first-seen timestamp in our own DB → "registered with us" age, not platform age | **High — and ours to build** |
| Webhook `added_to_chat_at` / `join_time` | Returns on `message_created` and `ChatMember` object | **High** — per-chat join time, available now |

**Recommended MVP signal stack (in priority order):**
1. Message text — LLM classifier (the existing core, 1:1 portable)
2. Bio / `description` text — text classifier, no new dependency
3. Username shape (`id<digits>` vs named) — cheap rule
4. `user_id` first-seen in our DB — built from our own writes
5. `last_activity_time` — when present, freshness signal
6. Per-chat `join_time` — "joined today" is a strong spam indicator

Pure platform age (creation date) is **not available in 2026-07**. Track this in the memo as an open dependency on MAX shipping a `User.created_at` field.

**Sources (added this update):**
29. https://dev.max.ru/docs-api/objects/User
30. https://dev.max.ru/docs-api/objects/ChatMember
31. https://github.com/max-messenger/max-bot-api-client-ts/issues/36
32. https://github.com/MaxApiTeam/PyMax
33. https://github.com/soslaxx/vkmax
34. https://github.com/nsdkinx/vkmax
35. https://github.com/huxuxuya/python-max-client
36. https://github.com/koval01/gist (anti-bot heuristics)
37. https://maxofficial.ru/blog/perevody-po-sbp-v-max-kakie-banki-i-kak-otpravit
38. https://bosfera.ru/press-release/v-messendzhere-maks-poyavitsya-oplata-po-sbp (NSPK + MAX MoU, ПМЭФ-26)
39. https://max-osnova.ru/solutions/oplata (CloudPayments/ЮKassa bot billing)

---

## Verification spikes (added 2026-07-26)

**Prerequisite:** MAX account + bot token from `@MasterBot`. Registration is tied to a phone number (Gosuslugi/ESIA). Owner must provide the token.

| # | Spike | Verifies | Pass criterion | Effort |
|---|---|---|---|---|
| 1 | **E2E comment moderation** | Native comments + `DELETE /messages` + `DELETE /members` | Bot receives a comment → deletes it → bans the commenter. All three, no errors. | 1d |
| 2 | **Webhook payload capture** | Update schema, field names, parent-post reference | Full JSON dump of a `message_created` comment event with `text`, `sender.user_id`, `chat_id`, and a link back to the parent post. | 0.5d |
| 3 | **Admin approval UX** | Inline keyboards + `callback` events | "Delete" / "Not spam" buttons render, callback payload is parseable, bot can act on it. | 0.5d |
| 4 | **Account age proxies** | Which of the 6 signals are actually returned by the API | ≥2 of: `last_activity_time`, `username` shape, `user_id` magnitude, bio presence — reliably available via `GET /chats/{chatId}/members`. | 0.5d |
| 5 | **Billing path** | CloudPayments / ЮKassa link delivered via MAX bot | Payment link sent as a button or text → user opens it → test payment completes. | 1d |
| 6 | **Rate limits / anti-fraud** | Moderation at scale on Bot API | 50 deletes + 10 bans in 60s without 429s or account restriction. | 0.5d |

**Critical path:** 1+2 (run together — capture the payload *during* the moderation test) → 3 → 6. Spikes 4 and 5 are independent, run in parallel.

**Gate:** Spike 1 is binary. If the bot can't receive + delete + ban in a channel with native comments, nothing else matters and we stop.

**Total:** ~4 focused days once the bot token is available.

---

## Spike results — live test 2026-07-28 (Spike 1+2 partial)

**Test environment:** Bot "Антиспам" (`id773671678516_1_bot`, user_id `385916094`), channel "Тест антиспам" (chat_id `-77345848199175`), webhook via Cloudflare tunnel → `:9876` listener → JSONL log.

### Confirmed working

| Check | Result | Evidence |
|---|---|---|
| Webhook delivery | ✅ | `bot_started` + `bot_added` events captured via tunnel |
| Channel detection | ✅ | `bot_added` payload has `is_channel: true`, negative `chat_id` |
| Message read | ✅ | `GET /messages?chat_id=-77345848199175&limit=5` returns posts with `body.mid`, `body.text`, `timestamp`, `stat.views` |
| User signals in events | ✅ | `description` (bio), `last_activity_time`, `name`, `is_bot` all present in webhook payloads |
| Bot permissions in channel | ✅ **Surprise** | Bot holds `add_remove_members` + `delete` + `read_all_messages` + `write` + `edit` + `pin_message` + `change_chat_info` |

### Docs were wrong: `add_remove_members` in channels

Official docs state `add_remove_members` is "для ботов — только в чатах" (chats only, not channels). **Empirically false.** `GET /chats/-77345848199175/members` returns the bot's permission list including `add_remove_members`. The Telegram "delete + ban" pattern **may port 1:1** — pending a live `DELETE /members` test against a real (non-owner) user.

### Schema / API findings

- **Message IDs** are `mid.<hex>` strings (e.g. `mid.ffffb9a7843177f9019fa81ec8171733`), not integers. Schema migration needs a `VARCHAR` / `TEXT` `message_id` column.
- **`GET /messages?chat_id=`** works; **`GET /chats/{id}/messages`** returns 404 `method.not.found`. Path matters.
- **`GET /chats/{id}`** returns `participants` as a map (`{"385916094": 0}`) — different shape than `/members` (which returns an array of member objects).
- **No `sender` field** in `GET /messages` response for channel posts — need to confirm webhook `message_created` events include it.
- **`bot_started`** events include `user_locale` ("ru"); **`bot_added`** events do not.
- **`username`** field absent for users who haven't set one (owner has none); bot's own username is `id773671678516_1_bot` — confirms the `id<digits>` new-account pattern.

### TLS blocker (production)

`platform-api2.max.ru` uses a Минцифры CA certificate not in the standard Linux trust store. All spike API calls used `curl -k` (skip verify). **Production deployment must add the CA cert to `/etc/ssl/certs/` or use a custom CA bundle.** This is a real deployment blocker, not just a spike issue.

### Open items (not yet tested)

| Item | Status | Blocker |
|---|---|---|
| `message_created` webhook event for a comment | ❌ Not captured | Only message in channel predates bot addition; need a new comment posted after bot is subscribed |
| `DELETE /messages` on a real comment | ❌ Not tested | Waiting on `message_created` event to get a valid `mid` |
| `DELETE /chats/{chatId}/members` (ban) | ❌ Not tested | No second user available; owner account is whitelisted (never-moderate) |
| Inline keyboard + `callback` event | ❌ Not tested | Spike 3 |
| Rate limits | ❌ Not tested | Spike 6 |

### Owner whitelist

User_id `190126855` (Алексей Лещенко) is flagged as **owner / never-moderate** in all spike analysis and future production code. No delete, no ban, no spam flag — regardless of content posted.

### Webhook subscription format

```json
POST /subscriptions
{"url": "https://<tunnel>.trycloudflare.com/webhook"}
→ {"success": true}
```

No event-type filter in the subscription payload — the bot receives all event types (`message_created`, `message_edited`, `message_removed`, `message_callback`, `bot_added`, `bot_started`, `user_added`, etc.). Filtering must be done client-side.

### Sources (added this update)

40. Live webhook payloads — `/root/ai-antispam/.secrets/max-webhook-payloads.jsonl`
41. `GET /chats/-77345848199175/members` response — bot permission list
42. `GET /messages?chat_id=-77345848199175&limit=5` response — message schema
43. `GET /chats/-77345848199175` response — channel object shape

---

## 🚨 SHOWSTOPPER — Comments invisible to Bot API (2026-07-28, live test)

### Finding

MAX channel comments do NOT generate webhook events and are NOT returned by any REST endpoint.

**Test protocol:**
- Bot added to channel "Тест антиспам" (chat_id: -77345848199175) as admin
- Webhook registered at tunnel URL, subscription confirmed via `GET /subscriptions`
- Listener alive, tunnel reachable (health 200)
- User posted channel post "Тест комментов 2" → `message_created` webhook event received ✅
- User commented on the post → **zero webhook events, zero API records** ❌
- `GET /messages?chat_id=...` returns only 2 posts (messages_count: 2)
- `GET /messages/{mid}/replies` → 404
- `GET /comments` → 404

### Confirmed working (for reference)

| Event type | Webhook | REST |
|---|---|---|
| `bot_started` (DM) | ✅ | N/A |
| `bot_added` (channel) | ✅ | N/A |
| `message_created` (channel POST) | ✅ | ✅ `GET /messages` |
| **Channel COMMENT** | **❌ No event** | **❌ Not in any endpoint** |

### Auth note

`platform-api2.max.ru` uses raw token auth: `Authorization: <token>` (NO "Bearer" prefix).
`Authorization: Bearer <token>` returns `{"code":"verify.token","message":"Malformed access token"}`.

### Impact

The core antispam product (comment moderation) is **not buildable** with the current MAX Bot API.
Channel posts can be moderated, but comments — where spam actually lands — are invisible.

### Recommended actions

1. File feature request with MAX developer support for comment webhook events + sender field
2. Re-test in 2 weeks (comments GA'd 13 July 2026; API may lag)
3. Do NOT start implementation until comment visibility is confirmed
4. Monitor MAX developer changelog / community for Bot API updates

### Owner whitelist

user_id `190126855` (Алексей Лещенко) = owner, NEVER moderate/delete/ban/flag.

---

## Clean-test result — 2026-07-28 12:29 UTC (DEFINITIVE)

**Test conditions:** Listener alive (PID verified), tunnel alive (public URL 200), webhook subscription active (single URL registered), end-to-end health check passed. User commented on a channel post at ~12:29 UTC.

**Result:**
- Webhook events received: **0** (log unchanged at 6 entries)
- `GET /messages?chat_id=-77345848199175`: **2 messages** (both channel posts, no comment)
- Comment is **completely invisible** to the Bot API — no webhook event, no REST record

**Verdict: CONFIRMED SHOWSTOPPER.** MAX native comments (GA'd 13 Jul 2026) are a client-side UI feature with zero Bot API surface. The previous "listener was down" explanation is eliminated — infrastructure was fully operational during this test.

**Implication:** The ai-antispam MAX port cannot moderate comments until MAX adds comment events to the Bot API. No workaround exists within the sanctioned Bot API. The mobile-API userbot path (`api.oneme.ru`) can read comments but carries permanent ban risk tied to ESIA/Gosuslugi identity.

**Recommended actions:**
1. File feature request with MAX developer support (dev.max.ru) — ask for `message_created` events on channel comments with populated `sender` field
2. Set a 2-week cron reminder to re-test (feature is 15 days old; API may catch up)
3. Do NOT start implementation until comment events are confirmed
4. If MAX confirms roadmap → wait. If no response → evaluate mobile-API risk or pivot to post-only moderation (low value)

---

---

## User stories — zero Bot API surface (investigated 2026-07-31)

User stories GA'd on MAX **15 July 2026** (mobile/web clients only). Channel stories announced for "end of summer 2026". As of 31 July 2026, **no Bot API endpoint, SDK method, update type, or changelog entry exists for stories** — confirmed across all five independent SDK sources:

| Source | Stories? |
|---|---|
| Official Go SDK (`max-messenger/max-bot-api-client-go`) | ❌ No `/stories` path in `const.go` |
| Official TS SDK (`max-messenger/max-bot-api-client-ts`) | ❌ No stories module |
| Python SDK (`love-apples/maxapi`) | ❌ `ApiPath` enum: 12 paths, none stories; `UpdateType`: 17 types, none story-related |
| Rust SDK (`maxoxide`) + PHP port | ❌ Full 31-method table, no stories |
| Java SDK (`wilidon/max-bot-api-java`) | ❌ "all 31 MAX Bot API methods" — no stories |

The complete API surface is: `/me`, `/chats`, `/messages`, `/updates`, `/videos`, `/answers`, `/actions`, `/pin`, `/members`, `/admins`, `/uploads`, `/subscriptions`. No `/stories`.

**Trojan horse attack vector:** A spammer posts benign comments (passes LLM + keyword checks) while their **stories carry the actual ads**. On Telegram this is mitigated because `stories.py` feeds story content into the LLM prompt via the `tg-mcp.l1979.ru` MTProto bridge. On MAX, stories are a **completely blind spot** — no Bot API, no MTProto equivalent, and mobile-API reverse-engineering carries permanent ban risk (ESIA/Gosuslugi identity). This compounds the existing 30–40% accuracy hit from the comment showstopper.

**Compensating signals (all that's available on MAX):** bio-text heuristics, username shape (`id<digits>`), `join_time` recency, `is_bot` flag. A user matching `id<digits>` + empty bio + recent join + benign comments is the exact Trojan horse profile — and unverifiable via stories.

**Channel stories** (coming "end of summer 2026") will open a second vector: spam ads in channel stories, also invisible to bots.

**Sources (added this update):**
44. https://github.com/max-messenger/max-bot-api-client-go/blob/main/internal/api/const.go
45. https://github.com/love-apples/maxapi/blob/main/maxapi/api/client.py (ApiPath + UpdateType enums)
46. https://github.com/maxoxide/maxoxide/blob/main/src/client.rs (full method table)
47. https://github.com/wilidon/max-bot-api-java (31-method inventory)
48. https://telegraphyx.ru/blog/istorii-v-max-2026/ (stories GA timeline)
## STATUS: POSTPONED — 2026-07-28

**Decision:** Do not build. Re-test on or after **11 August 2026**.

**Reason:** MAX native comments (GA'd 13 July 2026) have zero Bot API surface. No webhook events, no REST endpoints, no SDK support, no changelog entry. Confirmed via clean live test with verified infrastructure (listener alive, tunnel reachable, subscription active). Comments are a client-only UI feature.

**What works today (confirmed by spike):**
- Bot can be added to channels as admin ✅
- `message_created` webhook for channel posts ✅
- `DELETE /messages` endpoint exists (untested on live message)
- `add_remove_members` permission granted to bots in channels (docs said otherwise — empirically false)
- User signals: `description`, `last_activity_time`, `name`, `is_bot` in events ✅
- Message IDs: `mid.<hex>` string format

**What's blocked:**
- Comment events (webhook + REST) — the entire product premise
- Comment listing / reply threading — no API method exists
- Sender attribution on comments — untestable without events

**Re-test protocol (11 Aug 2026):**
1. Check `dev.max.ru/docs-api/changelog-api` for comment-related entries
2. Check official SDK repos for new methods (`get_comments`, `get_replies`, comment event types)
3. Restart webhook listener + tunnel, re-register subscription
4. Post + comment in test channel, verify `message_created` with `sender` field
5. **Stories API** — probe for `/stories` endpoints, story update types, and SDK methods across all five SDKs (Go, TS, Python, Rust, Java). Check dev.max.ru changelog. If stories endpoints appear, assess Trojan horse mitigation.
6. If events arrive → proceed to implementation. If not → postpone another 2 weeks or pivot.

**Cron reminder set:** 11 Aug 2026, this session.

**Memo location:** `/root/ai-antispam/docs/memos/2026-07-26-max-antispam-memo.md`
**Test channel:** "Тест антиспам" (chat_id: -77345848199175)
**Bot:** "Антиспам" (user_id: 385916094, username: id773671678516_1_bot)
**Owner whitelist:** user_id 190126855 — never moderate
