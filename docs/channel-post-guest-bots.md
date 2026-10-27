# Channel post draft — guest bots ("hire a bot to say it")

**Channel:** @ai_antispam
**Status:** DRAFT — **the owner publishes this.** The agent has no publish path to the channel and does not acquire one in this change.
**Describes deployed code:** `ef7a031`, run [40245529519](https://github.com/leshchenko1979/ai-antispam/actions/runs/40245529519) — success; container restarted `2026-10-25T23:41:47Z`.
**Revision:** rev.2, 2026-10-27 — rewritten against `docs/channel-post-guest-bots-review.md` (three-reader review, `9a2693c`).

---

## RU (primary)

**Гостевые боты: почему спам теперь приходит не с аккаунта спамера**

**Что произошло.** По порядку, как это выглядело в группе:

1. Участник пишет сообщение, в котором нет ничего, кроме трёх упоминаний: `@bot1 @bot2 @bot3`. Ни текста, ни картинки, ни ссылки.
2. Через **9,4 секунды** отвечает первый бот — реклама обменника USDT.
3. Через **0,05 секунды** — второй, с тем же текстом.
4. Ещё через **0,04 секунды** — третий, тот же текст с другим вступлением.

То есть: одно сообщение участника, пауза в девять секунд — и три рекламных ответа в течение **0,09 секунды**. Ни один из трёх ботов в группе не состоял: каждый ответил ровно один раз и больше не появлялся. Сам участник рекламы не писал — только упоминания.

Девять секунд — это не человек, который успел прочитать и ответить. Это автоматика.

**Что изменилось в Telegram.** В мае 2026 года появились **гостевые боты**. Раньше бот не мог заговорить в чужой группе сам, от своего имени: чтобы он там писал, его должен добавить администратор, а во многих группах добавление ботов вообще запрещено. Теперь достаточно упомянуть бота по имени — и он отвечает в чате, участником которого **не является**. Ни приглашения, ни прав администратора, ни согласия владельца чата. В одном сообщении можно позвать до трёх ботов сразу — в том случае спамер использовал ровно три, то есть максимум.

**Почему это выгодно спамеру.** Вся суть в цене:

- **Обычный аккаунт требует телефонного номера.** Забанили — номер потрачен, нужен новый. Это дорого и конечно.
- **Бот не стоит ничего.** С одного аккаунта можно создать десятки ботов, отдельный номер для каждого не нужен.
- **Раньше спамер не мог завести своего бота в чужой чат** — у него не было на это права. Теперь может, и не одного.
- **Каждый такой бот — свежий**: его нет ни в одном чёрном списке. Свой аккаунт спамер при этом не подставляет.

Поэтому чёрные списки ботов тут бесполезны: список не догонит — боты бесплатны и их можно наплодить заново. Стоит в этой схеме только аккаунт, который их вызывает.

**Что мы сделали.** Бот смотрит, кто вызвал бота, и проверяет такое сообщение как обычное — вместе с текстом и ссылками. Само упоминание бота нарушением не считается: смотрят на то, что бот ответил.

Если ответ — спам, происходит вот что: **рекламные ответы удаляются, а аккаунт, который вызвал ботов, получает бан в группе — постоянный, не на сутки.** Бот — расходник, его не жалко; аккаунт, который раз за разом вызывает новых ботов, — не расходник, он стоит номера. Именно его и надо выключать. (При включённом автоудалении бот делает это сразу; в режиме по умолчанию присылает вам уведомление с кнопкой «Удалить».)

Администраторов и доверенных участников это не касается: если бота позвал админ, сообщение не удаляется.

**Можно ли закрыть это целиком.** Можно, одним переключателем: в группах с включённой защитой контента гостевые боты не работают вовсе. Цена очевидна — участники не смогут пересылать и сохранять сообщения. Если такой ценой платить не хочется, наш бот ловит инициатора: того, кто зовёт ботов.

**Добавьте бота в группу — он проверит такие сообщения.** [@ai_spam_blocker_bot](https://t.me/ai_spam_blocker_bot) · [ai-antispam.ru](https://ai-antispam.ru)

---

## Notes for the owner (not part of the post)

- **Publish order is load-bearing.** This post describes behaviour that is deployed, not behaviour observed in production. Legs verified: run `40245529519` green on `ef7a031`, container `StartedAt 2026-10-25T23:41:47Z` (after the commit's own instant `2026-10-25T23:26:57Z`), and the new predicate present in the running container.
- **`#122` is regression-proven on live traffic.** Since the swap: **12 bans and 12 message deletions**, every pair matched, from `23:51:20Z` through `09:26:58Z`. Ordinary spam still gets classified, banned and removed.
- **The warning this issue was opened for has fired 0 times since the swap** — `not found in info`: 0, `Message lookup cache miss`: 0, `Origin carries no group message`: 0. Two WARNING lines exist in that window and neither is a moderation warning: one is `Server started`, the other is a credits notice for a chat with no paying admins.
- **Not yet measured:** zero guest-bot payloads have arrived since the swap, so the end-to-end path has not been exercised by real traffic. `docs/spamTactics.md` tactic 12 states this in its own Mitigation clause and gets rewritten when one arrives. If you would rather publish only after a live catch, hold this draft until then — nothing in it becomes false by waiting.
- **Mechanic facts, sourced 2026-10-26** (the post states them as plain facts; these are the receipts): guest mode shipped in Bot API 10.0 on 2026-05-08 (`core.telegram.org/bots/api-changelog`); a guest bot replies **once** in a chat it has not joined, "without being added as a member", and "up to 3 guest bots" may be mentioned in one message (`core.telegram.org/bots/features`); guest bots cannot be queried in groups with content protection enabled (`core.telegram.org/api/bots/guest-mode`).
- **Bot cap, verified 2026-10-26:** BotFather allows **20 bots per account, 40 with Premium** (`BOT_CREATE_LIMIT_EXCEEDED` against `bots_create_limit_default` / `_premium`). rev.1 said «сколько угодно ботов»; rev.2 says «десятки», which is true and still makes the point.
- **Nothing identifying is in the post:** no chat id, no admin id, no victim handle, no message ids, no bot usernames (the three mentions are shown as `@bot1 @bot2 @bot3`).
- **The incident timeline is measured, not narrated.** Logfire rows for update ids `726166377`–`726166380`, read 2026-10-26: summon at `09:02:46.406Z` (a human, message `17835`, text = the three mentions and nothing else); replies at `09:02:55.780Z` (bot `8839849835`, caller `6350489404`), `09:02:55.833Z` (`8694772380`), `09:02:55.868Z` (`8967879593`). Deltas: 9.374 s / 0.053 s / 0.035 s, first-to-last 0.088 s. Every bot row carries `guest_bot_caller_user = 6350489404`, the summoner — which is the whole point of the fix.
- **What the punishment actually is, verified in code 2026-10-27:** `handle_spam.py:190-201` runs deletion and `ban_user_for_spam` in one `asyncio.gather`; `:632` calls `bot.ban_chat_member(chat_id, user_id)` with **no `until_date`**, so the ban is permanent; the user id passed is `effective_user_id`, which for a guest bot resolves to the summoner (`common/utils.py:259`).
- **The mode clause is not decoration.** Default mode is `NOTIFY` (`database/admin_operations.py:349`), so "the ads are deleted and the summoner is banned" is automatic only when the admin enabled auto-delete; otherwise the admin presses Delete. rev.1 implied it always happened automatically — that would have been a false claim about our own product, so rev.2 states both paths in one parenthesis.
- **Content protection is back, as ONE clause — this is your call.** You cut the "как проверить свою группу" section on 2026-10-26 and that stands; no numbered how-to is back. But the review flagged the omission as self-serving: the one toggle that kills the attack outright was absent while the post recommends our bot. rev.2 gives it a single honest sentence with its cost stated. Strike that paragraph if you disagree — nothing else in the post depends on it.
- **Deliberately not stated (owner instructions, 2026-10-26):** no Bot API terminology (the post speaks about Telegram as a user sees it), and no description of how such messages were handled before this change. The post explains the mechanic, the spammer's economics and the current behaviour. Do not reintroduce either when editing.
- **Review items applied in rev.2** (`docs/channel-post-guest-bots-review.md`): 1 the 0.09 s conflation split into 9.4 s + 0.09 s; 2 the broken "стоит телефонного номера" sentence rewritten; 3 "аккаунты бесплатны" no longer contradicts the phone-number premise; 4 "сколько угодно" → "десятки"; 5 the payoff added (ads deleted, summoner banned); 6 "наказание" replaced by the actual measure — permanent ban; 7 a clause for the ordinary member who mentions a useful bot; 8 "появился" → "ответил … и больше не появлялся"; 9 the admin exemption moved up, so the post no longer ends on a carve-out; 10 the footer gained a verb; 11 "в мае этого года" → "в мае 2026 года"; 13 five bullets → four; 14 "отстаёт на один аккаунт" → "не догонит: боты бесплатны". Item 12's inline-mode nuance is handled by "не мог заговорить … сам, от своего имени".
- No publish path was added or invoked: no send tool, no bot-token call, no broadcast script.
