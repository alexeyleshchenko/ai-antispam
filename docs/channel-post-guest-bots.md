# Channel post draft — guest bots ("hire a bot to say it")

**Channel:** @ai_antispam
**Status:** DRAFT — **the owner publishes this.** The agent has no publish path to the channel and does not acquire one in this change.
**Describes deployed code:** `ef7a031`, run [40245529519](https://github.com/leshchenko1979/ai-antispam/actions/runs/40245529519) — success; container restarted `2026-10-25T23:41:47Z`.

---

## RU (primary)

**Гостевые боты: почему спам теперь приходит не с аккаунта спамера**

**Что произошло.** По порядку, как это выглядело в группе:

1. Участник пишет сообщение, в котором нет ничего, кроме трёх упоминаний: `@bot1 @bot2 @bot3`. Ни текста, ни картинки, ни ссылки.
2. Через **9,4 секунды** отвечает первый бот — реклама обменника USDT.
3. Через **0,05 секунды** — второй бот, с тем же текстом.
4. Ещё через **0,04 секунды** — третий, тот же текст с другим вступлением.

Итого: одно сообщение участника и три рекламных ответа за **0,09 секунды**. Все три — с аккаунтов, которых в группе никогда не было: каждый появился ровно один раз и больше не писал. Сам участник рекламы не писал вообще — только упоминания.

**Что изменилось в Telegram.** В мае этого года появились **гостевые боты**. Раньше бот мог писать только там, куда его добавили: чтобы бот оказался в чужой группе, его должен пустить администратор, а во многих группах добавление ботов вообще запрещено. Теперь достаточно упомянуть бота по имени — и он отвечает в чате, участником которого **не является**. Ни приглашения, ни прав администратора, ни согласия владельца чата. В одном сообщении можно позвать до трёх ботов сразу — в том случае спамер использовал ровно три, то есть максимум.

**Почему это выгодно спамеру.** Вся суть в цене аккаунта:

- Обычный аккаунт стоит **телефонного номера**. Забанили — номер потрачен, нужен новый. Это дорого и конечно.
- Бот не стоит ничего: из одного аккаунта можно создать **сколько угодно ботов**, отдельный номер для каждого не нужен.
- Раньше спамер не мог завести своего бота в чужой чат — у него не было на это права. Теперь может, и не одного, а сколько успеет.
- Каждый такой бот — свежий: его нет ни в одном чёрном списке. Забанили — следующий бесплатный.
- Свой аккаунт спамер при этом не подставляет: он пишет одну безобидную строчку и упоминает бота.

Поэтому чёрные списки ботов тут бесполезны — список всегда отстаёт на один аккаунт, а аккаунты бесплатны.

**Что мы сделали.** Бот смотрит, кто вызвал бота, и проверяет такое сообщение как обычное — вместе с текстом и ссылками.

И второе, что важнее: наказание получает **тот, кто вызвал бота**, а не одноразовый бот. Бот — расходник, его не жалко. Аккаунт, который раз за разом вызывает новых ботов, — не расходник: он стоит номера.

Администраторов и доверенных участников это не касается: если бота позвал админ, сообщение не удаляется — ровно как и сейчас, когда админ пишет что-то сам.

Бот: [@ai_spam_blocker_bot](https://t.me/ai_spam_blocker_bot) · Сайт: [ai-antispam.ru](https://ai-antispam.ru)

---

## Notes for the owner (not part of the post)

- **Publish order is load-bearing.** This post describes behaviour that is deployed, not behaviour observed in production. Legs verified: run `40245529519` green on `ef7a031`, container `StartedAt 2026-10-25T23:41:47Z` (after the commit's own instant `2026-10-25T23:26:57Z`), and the new predicate present in the running container.
- **`#122` is regression-proven on live traffic.** Since the swap: **12 bans and 12 message deletions**, every pair matched, from `23:51:20Z` through `09:26:58Z`. Ordinary spam still gets classified, banned and removed.
- **The warning this issue was opened for has fired 0 times since the swap** — `not found in info`: 0, `Message lookup cache miss`: 0, `Origin carries no group message`: 0. Two WARNING lines exist in that window and neither is a moderation warning: one is `Server started`, the other is a credits notice for a chat with no paying admins.
- **Not yet measured:** zero guest-bot payloads have arrived since the swap, so the end-to-end path has not been exercised by real traffic. `docs/spamTactics.md` tactic 12 states this in its own Mitigation clause and gets rewritten when one arrives. If you would rather publish only after a live catch, hold this draft until then — nothing in it becomes false by waiting.
- **Mechanic facts, sourced 2026-10-26** (the post states them as plain facts; these are the receipts): guest mode shipped in Bot API 10.0 on 2026-05-08 (`core.telegram.org/bots/api-changelog`); a guest bot replies **once** in a chat it has not joined, "without being added as a member", and "up to 3 guest bots" may be mentioned in one message (`core.telegram.org/bots/features`); guest bots cannot be queried in groups with content protection enabled (`core.telegram.org/api/bots/guest-mode`).
- **Nothing identifying is in the post:** no chat id, no admin id, no victim handle, no message ids, no bot usernames (the three mentions are shown as `@bot1 @bot2 @bot3`).
- **The incident timeline is measured, not narrated.** Logfire rows for update ids `726166377`–`726166380`, read 2026-10-26: summon at `09:02:46.406Z` (a human, message `17835`, text = the three mentions and nothing else); replies at `09:02:55.780Z` (bot `8839849835`, caller `6350489404`), `09:02:55.833Z` (`8694772380`), `09:02:55.868Z` (`8967879593`). Deltas: 9.374 s / 0.053 s / 0.035 s, first-to-last 0.088 s. Every bot row carries `guest_bot_caller_user = 6350489404`, the summoner — which is the whole point of the fix.
- **The "how to check your group" section was cut on owner instruction (2026-10-26).** It read as a strange how-to bolted onto a post that explains a mechanic. Its one load-bearing fact — guest bots cannot be queried in groups with content protection enabled — stays in `docs/spamTactics.md` tactic 12, where an admin can find it without the post turning into a manual.
- **Deliberately not stated (owner instructions, 2026-10-26):** no Bot API terminology (the post speaks about Telegram as a user sees it), and no description of how such messages were handled before this change. The post explains the mechanic, the spammer's economics and the current behaviour. Do not reintroduce either when editing.
- No publish path was added or invoked: no send tool, no bot-token call, no broadcast script.
